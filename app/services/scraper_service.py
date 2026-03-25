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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger, set_correlation_id
from app.crawler.confidence import calculate_confidence, log_low_confidence_scores
from app.database import async_session_factory
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import normalize_partner_payload, schema_to_listing_dict
from app.services.parser_service import parse_listing_links, parse_listing_page, parse_next_page

logger = get_logger(__name__)

_CRITICAL_PARSER_FIELDS = ("title", "price", "property_type", "district")


def _missing_critical_parser_fields(raw_data: dict[str, Any]) -> list[str]:
    """Return critical parser fields that are absent or blank."""
    missing = []
    for field in _CRITICAL_PARSER_FIELDS:
        value = raw_data.get(field)
        if value is None or not str(value).strip():
            missing.append(field)
    return missing


def _stale_job_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=settings.scrape_job_stale_after_seconds)


async def recover_stale_jobs(db: AsyncSession) -> int:
    """Mark stalled running jobs as failed so the worker queue can recover."""
    stale_jobs = (
        await db.execute(
            select(ScrapeJob).where(
                ScrapeJob.status == "running",
                or_(
                    ScrapeJob.last_heartbeat_at.is_(None),
                    ScrapeJob.last_heartbeat_at < _stale_job_cutoff(),
                ),
            )
        )
    ).scalars().all()

    for job in stale_jobs:
        job.mark_failed("Job marked failed after stale heartbeat timeout.")

    if stale_jobs:
        await db.commit()
        logger.warning("Recovered %d stale scrape job(s)", len(stale_jobs))

    return len(stale_jobs)


async def run_scrape_job(job_id: str) -> None:
    """Entry point for background scraping job.

    FIX: Abre uma única sessão de DB para toda a duração do job, em vez de
    abrir/fechar uma sessão por cada operação auxiliar.
    """
    set_correlation_id(job_id)
    logger.info("Starting scrape job %s", job_id)

    # ÚNICA sessão para todo o job — todas as funções auxiliares recebem-na como argumento
    async with async_session_factory() as db:
        try:
            result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
            job = result.scalar_one_or_none()
            if not job:
                logger.error("Job %s not found", job_id)
                return

            if job.status == "cancelled":
                logger.info("Job %s was cancelled before execution started", job_id)
                return

            if job.status != "pending":
                logger.warning("Job %s is in status '%s' and will not be started again", job_id, job.status)
                return

            site_result = await db.execute(
                select(SiteConfig).where(
                    SiteConfig.key == job.site_key,
                    SiteConfig.is_active.is_(True),
                )
            )
            site_config = site_result.scalar_one_or_none()
            if not site_config:
                job.mark_failed(f"Site config '{job.site_key}' not found or inactive")
                await db.commit()
                return

            job.mark_running()
            await db.commit()

            await _run_scrape_async(
                db=db,
                job_id=str(job.id),
                site_key=job.site_key,
                base_url=site_config.base_url,
                start_url=job.start_url,
                max_pages=job.max_pages,
                selectors=site_config.selectors,
                extraction_mode=site_config.extraction_mode,
                link_pattern=site_config.link_pattern,
                image_filter=site_config.image_filter,
                image_exclude_filter=getattr(site_config, "image_exclude_filter", None),
                config=job.config or {},
                pagination_type=site_config.pagination_type,
                pagination_param=site_config.pagination_param,
                request_headers=getattr(site_config, "request_headers", None) or {},
            )

        except Exception as e:
            logger.error("Job %s failed: %s", job_id, str(e), exc_info=True)
            try:
                await db.rollback()
                result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
                job = result.scalar_one_or_none()
                if job and job.status == "running":
                    job.mark_failed(str(e))
                    await db.commit()
            except Exception:
                pass


async def _run_scrape_async(
    db: AsyncSession,
    job_id: str,
    site_key: str,
    base_url: str,
    start_url: str,
    max_pages: int,
    selectors: dict[str, Any],
    extraction_mode: str,
    link_pattern: str | None,
    image_filter: str | None,
    image_exclude_filter: str | None,
    config: dict[str, Any],
    pagination_type: str = "html_next",
    pagination_param: str | None = None,
    request_headers: dict[str, str] | None = None,
) -> None:
    """Async scraping loop — recebe a sessão DB existente em vez de abrir novas."""
    if extraction_mode not in ("direct", "section"):
        logger.warning("Unknown extraction_mode '%s' for job %s — defaulting to 'direct'", extraction_mode, job_id)
        extraction_mode = "direct"

    scraper = EthicalScraper(
        min_delay=config.get("min_delay") or settings.default_min_delay,
        max_delay=config.get("max_delay") or settings.default_max_delay,
        user_agent=config.get("user_agent") or settings.default_user_agent,
        timeout=settings.request_timeout,
        extra_headers=request_headers or {},
    )

    # Load the job object ONCE and reuse it throughout — eliminates N+1 SELECT queries.
    job_result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
    job = job_result.scalar_one_or_none()
    if not job:
        logger.error("Job %s not found at scrape start", job_id)
        return

    try:
        full_selectors = {**selectors}
        if link_pattern:
            full_selectors["listing_link_pattern"] = link_pattern
        if image_filter:
            full_selectors["image_filter"] = image_filter
        if image_exclude_filter:
            full_selectors["image_exclude_filter"] = image_exclude_filter

        current_url = start_url
        pages_visited = 0
        listings_found = 0
        listings_scraped = 0
        errors = 0

        job.touch_heartbeat()
        await db.commit()

        for page_num in range(max_pages):
            # Re-read only status/cancel fields — lightweight scalar query
            if await _check_job_cancelled(db, job_id):
                logger.info("Job %s was cancelled", job_id)
                break

            job.touch_heartbeat()
            await db.commit()

            logger.info("Scraping page %d: %s", page_num + 1, current_url)

            response = await asyncio.to_thread(scraper.get, current_url)
            if not response:
                logger.warning("Failed to fetch page: %s", current_url)
                job.add_log("error", f"Failed to fetch page: {current_url}", current_url)
                errors += 1
                job.update_progress(
                    pages_visited=pages_visited,
                    listings_found=listings_found,
                    listings_scraped=listings_scraped,
                    errors=errors,
                )
                job.touch_heartbeat()
                await db.commit()
                await _fail_job(db, job, f"Failed to fetch page: {current_url}")
                return

            pages_visited += 1
            html = response.text

            links = parse_listing_links(html, base_url, full_selectors)
            listings_found += len(links)

            # Batch-track found URLs — single commit per page
            for link in links:
                job.add_url("found", link)
            job.touch_heartbeat()
            await db.commit()

            for link in links:
                if await _check_job_cancelled(db, job_id):
                    break

                try:
                    detail_response = await asyncio.to_thread(scraper.get, link)
                    if not detail_response:
                        job.add_url("failed", link)
                        job.add_log("warning", "Failed to fetch listing page", link)
                        job.touch_heartbeat()
                        await db.commit()
                        continue

                    raw_data = parse_listing_page(
                        detail_response.text,
                        link,
                        full_selectors,
                        extraction_mode,
                    )

                    missing_fields = _missing_critical_parser_fields(raw_data)
                    if missing_fields:
                        job.add_log(
                            "warning",
                            f"Critical parser fields missing: {', '.join(missing_fields)}",
                            link,
                        )

                    property_schema = normalize_partner_payload(raw_data, site_key)
                    await _persist_listing(db, job_id, property_schema, site_key)
                    job.add_url("scraped", link)
                    listings_scraped += 1

                    job.update_progress(
                        pages_visited=pages_visited,
                        listings_found=listings_found,
                        listings_scraped=listings_scraped,
                        errors=errors,
                    )
                    job.touch_heartbeat()
                    await db.commit()

                except Exception as e:
                    logger.error("Error processing listing %s: %s", link, str(e))
                    await db.rollback()
                    job.add_url("failed", link)
                    job.add_log("error", f"Error processing listing: {str(e)}", link)
                    errors += 1
                    job.touch_heartbeat()
                    await db.commit()

            # ---------- PAGINATION UNIVERSAL ----------
            if pagination_type == "incremental_path":
                current_url = f"{start_url.rstrip('/')}/{page_num + 2}"
            elif pagination_type == "query_param" and pagination_param:
                sep = "&" if "?" in start_url else "?"
                current_url = f"{start_url}{sep}{pagination_param}={page_num + 2}"
            elif pagination_type == "html_next":
                next_url = parse_next_page(html, base_url, full_selectors)
                if not next_url:
                    logger.info("No more pages — stopping")
                    break
                current_url = next_url
            else:
                logger.warning("Unknown pagination_type %s — stopping", pagination_type)
                await _fail_job(db, job, f"Unknown pagination_type: {pagination_type}")
                return

        await _complete_job(db, job)

    except Exception as e:
        logger.error("Scraping error: %s\n%s", str(e), traceback.format_exc())
        await db.rollback()
        await _fail_job(db, job, str(e))
    finally:
        scraper.close()


# ---------------------------------------------------------------------------
# Funções auxiliares — recebem db: AsyncSession, sem abrir sessões próprias
# ---------------------------------------------------------------------------

async def _check_job_cancelled(db: AsyncSession, job_id: str) -> bool:
    """Lightweight check: only fetches status + cancel fields."""
    result = await db.execute(
        select(ScrapeJob.status, ScrapeJob.cancel_requested_at).where(ScrapeJob.id == UUID(job_id))
    )
    row = result.one_or_none()
    if row is None:
        return False
    status, cancel_requested_at = row
    return status == "cancelled" or cancel_requested_at is not None


async def _persist_listing(db: AsyncSession, job_id: str, schema, site_key: str) -> None:
    """Persist a listing atomically, using PostgreSQL upsert when possible."""
    listing_data = schema_to_listing_dict(schema, scrape_job_id=UUID(job_id))
    source_url = listing_data.get("source_url")

    # SQLAlchemy 2.x: inspect engine dialect via the session's bind
    try:
        dialect_name = db.get_bind().dialect.name
    except Exception:
        dialect_name = engine.dialect.name

    if source_url and dialect_name == "postgresql":
        await _persist_listing_with_postgres_upsert(db, job_id, schema, listing_data)
        return

    await _persist_listing_legacy(db, job_id, schema, listing_data)


async def _persist_listing_with_postgres_upsert(
    db: AsyncSession,
    job_id: str,
    schema,
    listing_data: dict[str, Any],
) -> None:
    """Persist a listing with lock-aware PostgreSQL conflict handling."""
    source_url = listing_data["source_url"]
    existing = (
        await db.execute(
            select(Listing)
            .where(Listing.source_url == source_url)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if existing is None:
        inserted_id = (
            await db.execute(
                pg_insert(Listing)
                .values(**listing_data)
                .on_conflict_do_nothing(index_elements=[Listing.source_url])
                .returning(Listing.id)
            )
        ).scalar_one_or_none()

        if inserted_id is not None:
            await _replace_media_assets(db, inserted_id, schema)
            await db.commit()
            return

        logger.info("Listing insert raced for %s; reloading winner", source_url)
        existing = (
            await db.execute(
                select(Listing)
                .where(Listing.source_url == source_url)
                .with_for_update()
            )
        ).scalar_one_or_none()

    if existing is None:
        raise RuntimeError(f"Failed to resolve listing persistence target for {source_url}")

    new_price = listing_data.get("price_amount")
    if (
        new_price is not None
        and existing.price_amount is not None
        and existing.price_amount != new_price
    ):
        db.add(
            PriceHistory(
                listing_id=existing.id,
                price_amount=existing.price_amount,
                price_currency=existing.price_currency or "EUR",
            )
        )
        logger.info(
            "Price change for %s: %s → %s",
            source_url,
            existing.price_amount,
            new_price,
        )

    logger.info("Updating existing listing: %s", source_url)
    for field, value in listing_data.items():
        if field not in ("scrape_job_id",) and value is not None:
            setattr(existing, field, value)

    existing.updated_at = datetime.now(timezone.utc)
    existing.scrape_job_id = UUID(job_id)

    await _replace_media_assets(db, existing.id, schema)
    await db.commit()


async def _persist_listing_legacy(
    db: AsyncSession,
    job_id: str,
    schema,
    listing_data: dict[str, Any],
) -> None:
    """Fallback persistence path for non-PostgreSQL environments."""
    existing = None
    if listing_data.get("source_url"):
        result = await db.execute(
            select(Listing).where(Listing.source_url == listing_data["source_url"])
        )
        existing = result.scalar_one_or_none()

    if existing:
        logger.info("Updating existing listing: %s", listing_data["source_url"])

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

        for field, value in listing_data.items():
            if field not in ("scrape_job_id",) and value is not None:
                setattr(existing, field, value)
        existing.updated_at = datetime.now(timezone.utc)
        existing.scrape_job_id = UUID(job_id)
        await _replace_media_assets(db, existing.id, schema)

    else:
        listing = Listing(**listing_data)
        db.add(listing)
        await db.flush()  # flush para obter o ID antes de adicionar media assets

        await _replace_media_assets(db, listing.id, schema)

    await db.commit()


async def _replace_media_assets(db: AsyncSession, listing_id: UUID, schema) -> None:
    """Replace listing media atomically so retries and upserts do not duplicate assets."""
    await db.execute(delete(MediaAsset).where(MediaAsset.listing_id == listing_id))

    for media in schema.media:
        db.add(
            MediaAsset(
                listing_id=listing_id,
                url=str(media.url),
                alt_text=media.alt_text,
                type=media.type or "photo",
                position=media.position,
            )
        )


async def _complete_job(db: AsyncSession, job: ScrapeJob) -> None:
    """Marca o job como completo."""
    if job and job.status == "running":
        if job.cancel_requested_at is not None:
            job.mark_cancelled()
            logger.info("Job %s cancelled successfully", job.id)
        else:
            await _update_site_confidence_scores(db, job.site_key, job.id)
            job.mark_completed()
            logger.info("Job %s completed successfully", job.id)
        await db.commit()


async def _update_site_confidence_scores(db: AsyncSession, site_key: str, job_uuid: UUID) -> None:
    """Persist field extraction confidence back to the site configuration."""
    listings = (
        await db.execute(select(Listing).where(Listing.scrape_job_id == job_uuid))
    ).scalars().all()
    scores = calculate_confidence(listings)

    site = (
        await db.execute(select(SiteConfig).where(SiteConfig.key == site_key))
    ).scalar_one_or_none()
    if site is None:
        return

    site.confidence_scores = scores
    log_low_confidence_scores(site_key, scores)


async def _fail_job(db: AsyncSession, job: ScrapeJob, error: str) -> None:
    """Marca o job como falhado."""
    await db.rollback()
    if job and job.status == "running":
        job.mark_failed(error)
        await db.commit()
        logger.error("Job %s failed: %s", job.id, error)