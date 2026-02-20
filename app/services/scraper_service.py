"""Scraper service — orchestrates scraping jobs via BackgroundTasks.

This service:
1. Runs as an async background task (scheduled via FastAPI BackgroundTasks)
2. Uses EthicalScraper for rate-limited, robots.txt-respecting HTTP requests
3. Parses HTML via parser_service
4. Normalizes via mapper_service
5. Persists to DB with deduplication (upsert on source_url)
6. Tracks price history on updates
7. Updates job progress in real-time

NOTE: Since EthicalScraper uses synchronous `requests`, we wrap blocking calls
with `asyncio.to_thread()` to avoid blocking the event loop.
"""
import asyncio
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger, set_correlation_id
from app.database import async_session_factory
from app.models.listing import Listing
from app.models.media import MediaAsset
from app.models.price_history import PriceHistory
from app.models.scrape_job import ScrapeJob
from app.models.site_config import SiteConfig
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import normalize_partner_payload, schema_to_listing_dict
from app.services.parser_service import parse_listing_links, parse_listing_page, parse_next_page

logger = get_logger(__name__)


async def run_scrape_job(job_id: str) -> None:
    """Entry point for background scraping job.
    
    This function is called by FastAPI BackgroundTasks after the HTTP response
    is sent. It runs entirely async, using asyncio.to_thread for blocking I/O
    operations (HTTP requests via requests library).
    """
    set_correlation_id(job_id)
    logger.info("Starting scrape job %s", job_id)

    async with async_session_factory() as db:
        try:
            # Load job
            result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
            job = result.scalar_one_or_none()
            if not job:
                logger.error("Job %s not found", job_id)
                return

            # Load site config
            site_result = await db.execute(
                select(SiteConfig).where(SiteConfig.key == job.site_key, SiteConfig.is_active.is_(True))
            )
            site_config = site_result.scalar_one_or_none()
            if not site_config:
                job.mark_failed(f"Site config '{job.site_key}' not found or inactive")
                await db.commit()
                return

            # Mark as running
            job.mark_running()
            await db.commit()

            # Run the actual scraping
            await _run_scrape_async(
                job_id=str(job.id),
                site_key=job.site_key,
                base_url=site_config.base_url,
                start_url=job.start_url,
                max_pages=job.max_pages,
                selectors=site_config.selectors,
                extraction_mode=site_config.extraction_mode,
                link_pattern=site_config.link_pattern,
                image_filter=site_config.image_filter,
                config=job.config or {},
            )

        except Exception as e:
            logger.error("Job %s failed: %s", job_id, str(e), exc_info=True)
            try:
                result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
                job = result.scalar_one_or_none()
                if job and job.status == "running":
                    job.mark_failed(str(e))
                    await db.commit()
            except Exception:
                pass


async def _run_scrape_async(
    job_id: str,
    site_key: str,
    base_url: str,
    start_url: str,
    max_pages: int,
    selectors: Dict[str, Any],
    extraction_mode: str,
    link_pattern: Optional[str],
    image_filter: Optional[str],
    config: Dict[str, Any],
) -> None:
    """Async scraping loop — wraps blocking HTTP calls with asyncio.to_thread."""
    
    # Initialize ethical scraper (handle None values in config)
    scraper = EthicalScraper(
        min_delay=config.get("min_delay") or settings.default_min_delay,
        max_delay=config.get("max_delay") or settings.default_max_delay,
        user_agent=config.get("user_agent") or settings.default_user_agent,
        timeout=settings.request_timeout,
    )

    try:
        # Merge selectors with link_pattern and image_filter
        full_selectors = {**selectors}
        if link_pattern:
            full_selectors["listing_link_pattern"] = link_pattern
        if image_filter:
            full_selectors["image_filter"] = image_filter

        current_url = start_url
        pages_visited = 0
        listings_found = 0
        listings_scraped = 0
        errors = 0

        for page_num in range(max_pages):
            # Check if job was cancelled
            if await _check_job_cancelled(job_id):
                logger.info("Job %s was cancelled", job_id)
                break

            logger.info("Scraping page %d: %s", page_num + 1, current_url)

            # Fetch listing page (blocking call wrapped in to_thread)
            response = await asyncio.to_thread(scraper.get, current_url)
            if not response:
                logger.warning("Failed to fetch page: %s", current_url)
                await _add_job_log(job_id, "error", f"Failed to fetch page: {current_url}", current_url)
                errors += 1
                break

            pages_visited += 1
            html = response.text

            # Extract listing links
            links = parse_listing_links(html, base_url, full_selectors)
            listings_found += len(links)

            # Track found URLs
            for link in links:
                await _track_url(job_id, "found", link)

            # Process each listing
            for link in links:
                # Check cancellation
                if await _check_job_cancelled(job_id):
                    break

                try:
                    # Fetch listing detail page (blocking call wrapped in to_thread)
                    detail_response = await asyncio.to_thread(scraper.get, link)
                    if not detail_response:
                        await _track_url(job_id, "failed", link)
                        await _add_job_log(job_id, "warning", f"Failed to fetch listing page", link)
                        continue

                    # Parse the listing page
                    raw_data = parse_listing_page(
                        detail_response.text,
                        link,
                        full_selectors,
                        extraction_mode,
                    )

                    # Normalize to canonical schema
                    property_schema = normalize_partner_payload(raw_data, site_key)

                    # Persist to DB (with deduplication)
                    await _persist_listing(job_id, property_schema, site_key)
                    await _track_url(job_id, "scraped", link)
                    listings_scraped += 1

                    # Update progress after each listing for real-time feedback
                    await _update_job_progress(
                        job_id,
                        pages_visited=pages_visited,
                        listings_found=listings_found,
                        listings_scraped=listings_scraped,
                        errors=errors,
                    )

                except Exception as e:
                    logger.error("Error processing listing %s: %s", link, str(e))
                    await _track_url(job_id, "failed", link)
                    await _add_job_log(job_id, "error", f"Error processing listing: {str(e)}", link)
                    errors += 1

            # Find next page
            next_url = parse_next_page(html, base_url, full_selectors)
            if not next_url:
                logger.info("No more pages — stopping")
                break
            current_url = next_url

        # Mark job as completed
        await _complete_job(job_id)

    except Exception as e:
        logger.error("Scraping error: %s\n%s", str(e), traceback.format_exc())
        await _fail_job(job_id, str(e))
    finally:
        scraper.close()


async def _check_job_cancelled(job_id: str) -> bool:
    """Check if the job has been cancelled."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob.status).where(ScrapeJob.id == UUID(job_id)))
        status = result.scalar_one_or_none()
        return status == "cancelled"


async def _add_job_log(job_id: str, level: str, message: str, url: Optional[str] = None) -> None:
    """Add a log entry to the job."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job:
            job.add_log(level, message, url)
            await db.commit()


async def _track_url(job_id: str, status: str, url: str) -> None:
    """Track URL processing status."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job:
            job.add_url(status, url)
            await db.commit()


async def _update_job_progress(job_id: str, **progress) -> None:
    """Update job progress counters in the DB."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job:
            job.update_progress(**progress)
            await db.commit()


async def _persist_listing(job_id: str, schema, site_key: str) -> None:
    """Persist a listing to the DB with deduplication on source_url.

    If a listing with the same source_url exists:
    - Update its fields (preserving created_at)
    - Record price change if price differs
    """
    from app.services.mapper_service import schema_to_listing_dict

    async with async_session_factory() as db:
        listing_data = schema_to_listing_dict(schema, scrape_job_id=UUID(job_id))

        # Check for existing listing by source_url
        existing = None
        if listing_data.get("source_url"):
            result = await db.execute(
                select(Listing).where(Listing.source_url == listing_data["source_url"])
            )
            existing = result.scalar_one_or_none()

        if existing:
            # Deduplication: update existing listing
            logger.info("Updating existing listing: %s", listing_data["source_url"])

            # Track price change
            new_price = listing_data.get("price_amount")
            if (
                new_price is not None
                and existing.price_amount is not None
                and existing.price_amount != new_price
            ):
                price_record = PriceHistory(
                    listing_id=existing.id,
                    price_amount=existing.price_amount,
                    price_currency=existing.price_currency or "EUR",
                )
                db.add(price_record)
                logger.info(
                    "Price change for %s: %s → %s",
                    listing_data["source_url"],
                    existing.price_amount,
                    new_price,
                )

            # Update fields (preserve created_at)
            for field, value in listing_data.items():
                if field not in ("scrape_job_id",) and value is not None:
                    setattr(existing, field, value)
            existing.updated_at = datetime.now(timezone.utc)
            existing.scrape_job_id = UUID(job_id)

        else:
            # New listing
            listing = Listing(**listing_data)
            db.add(listing)
            await db.flush()

            # Add media assets
            for media in schema.media:
                asset = MediaAsset(
                    listing_id=listing.id,
                    url=str(media.url),
                    alt_text=media.alt_text,
                    type=media.type or "photo",
                )
                db.add(asset)

        await db.commit()


async def _complete_job(job_id: str) -> None:
    """Mark the job as completed."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job and job.status == "running":
            job.mark_completed()
            await db.commit()
            logger.info("Job %s completed successfully", job_id)


async def _fail_job(job_id: str, error: str) -> None:
    """Mark the job as failed."""
    async with async_session_factory() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
        job = result.scalar_one_or_none()
        if job and job.status == "running":
            job.mark_failed(error)
            await db.commit()
            logger.error("Job %s failed: %s", job_id, error)
