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

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.core.lifecycle import GONE_STATUSES, STATUS_ACTIVE
from app.core.logging import get_logger, set_correlation_id
from app.crawler.confidence import calculate_confidence, log_low_confidence_scores
from app.database import async_session_factory, engine
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig
from app.repositories.listings_repository import ListingRepository
from app.repositories.sold_listing_repository import SoldListingRepository
from app.services.email_service import send_job_notification
from app.services.ethics_service import EthicalScraper
from app.services.playwright_scraper import PlaywrightScraper
from app.services.mapper_service import (
    is_junk_listing,
    missing_critical_schema_fields,
    normalize_partner_payload,
    schema_to_listing_dict,
)
from app.services.parser_service import parse_listing_links, parse_listing_page, parse_next_page
from app.utils.content_hash import CONTENT_FIELDS, compute_content_hash
from app.services.sitemap_service import fetch_sitemap_urls
from app.services.scrape_job_event_service import record_event

logger = get_logger(__name__)

# Boolean amenity flags derived from keyword presence: absence of a match is
# itself meaningful ("not found on this scrape"), unlike other scalar fields
# where a None can just mean a flaky/partial extraction. These must always be
# overwritten on update — otherwise a feature detected once (e.g. has_pool)
# can never be cleared by a later scrape where the site no longer mentions it.
_FEATURE_FLAG_FIELDS = frozenset({
    "has_garage", "has_elevator", "has_balcony", "has_air_conditioning", "has_pool", "has_garden",
})

# Some partner sitemaps never remove sold/reserved listings, so a URL
# confirmed sold keeps costing a full fetch (ethical delay + JS render) on
# every scrape forever. Once confirmed, skip it outright for this many days
# before re-checking — long enough to avoid re-paying the cost every run,
# short enough that a relisted property isn't missed indefinitely.
SOLD_URL_CACHE_TTL_DAYS = 14


async def recover_stale_jobs(db: AsyncSession) -> int:
    """Mark running jobs as failed if their heartbeat has gone silent.

    A job is only considered stale when its last_heartbeat_at is older than
    STALE_THRESHOLD_SECONDS. This avoids a split-brain problem in multi-instance
    deployments (e.g. Cloud Run autoscaling): a new instance that starts while
    another instance is actively running a job must not mark that job as failed
    just because it sees status='running' on startup.

    A job with a recent heartbeat is alive on another instance — leave it alone.
    A job with no heartbeat at all (never started) is always considered stale.
    """
    STALE_THRESHOLD_SECONDS = settings.scrape_job_stale_after_seconds
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS)

    stale_jobs = (
        await db.execute(
            select(ScrapeJob).where(
                ScrapeJob.status == "running",
                (ScrapeJob.last_heartbeat_at == None) | (ScrapeJob.last_heartbeat_at < cutoff),  # noqa: E711
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
            await record_event(db, job.id, "started", "Scrape worker started")
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
                image_exclude_filter=site_config.image_exclude_filter,
                config=job.config or {},
                pagination_type=site_config.pagination_type,
                pagination_param=site_config.pagination_param,
                request_headers=site_config.request_headers or {},
                use_js_render=site_config.use_js_render,
            )

        except Exception as e:
            logger.error("Job %s failed: %s", job_id, str(e), exc_info=True)
            try:
                await db.rollback()
                result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
                job = result.scalar_one_or_none()
                if job and job.status == "running":
                    job.mark_failed(str(e))
                    await record_event(db, job.id, "failed", str(e), level="error")
                    await db.commit()
            except Exception:
                logger.exception("Failed to mark job %s as failed during recovery", job_id)


async def _fetch_html(scraper: "EthicalScraper | PlaywrightScraper", url: str) -> str | None:
    """Unified fetch that works for both EthicalScraper and PlaywrightScraper.

    Returns the HTML string, or None on failure/block.
    EthicalScraper is synchronous so it runs in a thread; PlaywrightScraper is async.
    """
    if isinstance(scraper, PlaywrightScraper):
        return await scraper.get_html(url)
    response = await asyncio.to_thread(scraper.get, url)
    return response.text if response else None


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
    use_js_render: bool = False,
) -> None:
    """Async scraping loop — recebe a sessão DB existente em vez de abrir novas."""
    if extraction_mode not in ("direct", "section"):
        logger.warning("Unknown extraction_mode '%s' for job %s — defaulting to 'direct'", extraction_mode, job_id)
        extraction_mode = "direct"

    if use_js_render:
        scraper: EthicalScraper | PlaywrightScraper = PlaywrightScraper(
            min_delay=config.get("min_delay") or settings.default_min_delay,
            max_delay=config.get("max_delay") or settings.default_max_delay,
            timeout=settings.playwright_timeout,
            extra_headers=request_headers or {},
            content_ready_selector=selectors.get("title_selector") or selectors.get("price_selector"),
        )
        logger.info("Job %s using PlaywrightScraper (JS rendering enabled)", job_id)
    else:
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

    try:
        if not job:
            logger.error("Job %s not found at scrape start", job_id)
            return

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
        warnings = 0
        new_count = 0
        updated_count = 0

        job.touch_heartbeat()
        await db.commit()

        # ── Sitemap mode ────────────────────────────────────────────────────
        visited_page_urls: set[str] = set()   # pagination page URLs visited this job
        seen_listing_urls: set[str] = set()   # listing detail URLs seen across all pages
        if pagination_type == "sitemap":
            await _run_sitemap_scrape(
                db=db,
                job=job,
                job_id=job_id,
                site_key=site_key,
                sitemap_url=start_url,
                full_selectors=full_selectors,
                extraction_mode=extraction_mode,
                link_pattern=link_pattern,
                scraper=scraper,
            )
            return
        # ────────────────────────────────────────────────────────────────────

        job_cancelled = False
        pagination_incomplete = False
        for page_num in range(max_pages):
            # Re-read only status/cancel fields — lightweight scalar query
            if await _check_job_cancelled(db, job_id):
                logger.info("Job %s was cancelled", job_id)
                job_cancelled = True
                break

            job.touch_heartbeat()
            await db.commit()

            logger.info("Scraping page %d: %s", page_num + 1, current_url)

            # Guard: stop if we've already visited this pagination URL this job.
            # Uses a local set so it works for both EthicalScraper and PlaywrightScraper
            # and catches URL normalization variants (e.g. ordem-1/pagina-1 vs pagina-1
            # resolving to the same page via a mis-configured next_page_selector).
            if current_url in visited_page_urls:
                logger.info("Pagination page already visited — stopping: %s", current_url)
                break
            visited_page_urls.add(current_url)

            html = await _fetch_html(scraper, current_url)
            if not html:
                logger.warning("Failed to fetch page: %s", current_url)
                errors += 1
                job.update_progress(
                    pages_visited=pages_visited,
                    listings_found=listings_found,
                    listings_scraped=listings_scraped,
                    errors=errors,
                )
                job.touch_heartbeat()

                if page_num == 0:
                    # The very first page is unreachable — there is nothing to
                    # salvage and no way to tell a misconfigured start_url from
                    # a site that's fully down, so this is a genuine failure.
                    job.add_log("error", f"Failed to fetch page: {current_url}", current_url)
                    await db.commit()
                    await _fail_job(db, job, f"Failed to fetch page: {current_url}")
                    return

                # A later page failing is a transient blip (timeout, momentary
                # block), not proof the whole crawl is broken. Stop paginating
                # here but still complete the job with whatever was already
                # scraped, instead of discarding a mostly-successful run.
                # seen_listing_urls is now an incomplete view of what's live on
                # the site (we never reached the remaining pages), so the
                # auto-delete safety net must be skipped — same reasoning as a
                # cancelled job.
                job.add_log(
                    "warning",
                    f"Failed to fetch page {page_num + 1} — stopping pagination early, "
                    f"job will complete with partial results: {current_url}",
                    current_url,
                )
                await db.commit()
                pagination_incomplete = True
                break

            pages_visited += 1

            links = parse_listing_links(html, base_url, full_selectors)

            # Deduplicate listing URLs across pages: if the paginator cycles or
            # two pagination page URLs serve identical content, avoid double-counting
            # listings_found and redundant per-listing HTTP fetches.
            new_links = [link for link in links if link not in seen_listing_urls]
            seen_listing_urls.update(new_links)

            duplicate_count = len(links) - len(new_links)
            if duplicate_count:
                logger.info(
                    "Page %d: %d links found, %d already seen — skipping duplicates",
                    page_num + 1, len(links), duplicate_count,
                )

            listings_found += len(new_links)

            # Batch-track found URLs — single commit per page
            for link in new_links:
                job.add_url("found", link)
            job.touch_heartbeat()
            await db.commit()

            for link in new_links:
                if await _check_job_cancelled(db, job_id):
                    job_cancelled = True
                    break

                scraped, errored, warned, is_new = await _process_listing_url(
                    db, job, job_id, site_key, scraper, link, full_selectors, extraction_mode
                )
                if scraped:
                    listings_scraped += 1
                    if is_new:
                        new_count += 1
                    else:
                        updated_count += 1
                if errored:
                    errors += 1
                if warned:
                    warnings += 1
                if scraped or errored or warned:
                    job.update_progress(
                        pages_visited=pages_visited,
                        listings_found=listings_found,
                        listings_scraped=listings_scraped,
                        errors=errors,
                        warnings=warnings,
                        new_listings=new_count,
                        updated_listings=updated_count,
                    )

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
                # Guard: if the selector is too broad and returns an already-visited
                # URL (e.g. a "previous" page link), treat it as end-of-pagination.
                if next_url in visited_page_urls:
                    logger.info("Next-page URL already visited — stopping: %s", next_url)
                    break
                current_url = next_url
            else:
                logger.warning("Unknown pagination_type %s — stopping", pagination_type)
                await _fail_job(db, job, f"Unknown pagination_type: {pagination_type}")
                return
        deleted_count = 0
        if job_cancelled or pagination_incomplete:
            reason = (
                "job was cancelled"
                if job_cancelled
                else "pagination stopped early after a page fetch failure"
            )
            logger.info("Job %s: skipping auto-delete — %s", job_id, reason)
            job.add_log("info", f"Auto-delete skipped: {reason} before the crawl finished")
        else:
            deleted_count = await _remove_missing_listings(
                db=db,
                job=job,
                site_key=site_key,
                discovered_urls=seen_listing_urls,
            )
        # Sempre actualiza o progress final antes de completar o job —
        # independentemente de ter havido deletes ou não.
        # Garante que pages_visited reflecte as páginas realmente visitadas
        # e não o último valor intermédio guardado dentro do loop.
        job.update_progress(
            pages_visited=pages_visited,
            listings_found=listings_found,
            listings_scraped=listings_scraped,
            errors=errors,
            warnings=warnings,
            new_listings=new_count,
            updated_listings=updated_count,
            deleted_listings=deleted_count,
        )
        await db.commit()
        await _complete_job(db, job)

    except Exception as e:
        logger.error("Scraping error: %s\n%s", str(e), traceback.format_exc())
        await db.rollback()
        if job:
            await _fail_job(db, job, str(e))
    finally:
        if isinstance(scraper, PlaywrightScraper):
            await scraper.close()
        else:
            scraper.close()


# ---------------------------------------------------------------------------
# Funções auxiliares — recebem db: AsyncSession, sem abrir sessões próprias
# ---------------------------------------------------------------------------

async def _run_sitemap_scrape(
    db: AsyncSession,
    job: ScrapeJob,
    job_id: str,
    site_key: str,
    sitemap_url: str,
    full_selectors: dict[str, Any],
    extraction_mode: str,
    link_pattern: str | None,
    scraper: "EthicalScraper | PlaywrightScraper",
) -> None:
    """Fetch all property URLs from a sitemap XML and scrape each detail page."""
    logger.info("Sitemap mode — fetching: %s", sitemap_url)

    # Sitemap XML is always static — use EthicalScraper regardless of JS mode.
    # If scraper is a PlaywrightScraper, create a dedicated EthicalScraper for the
    # sitemap fetch and close it when done (owned_sitemap_scraper=True).
    owned_sitemap_scraper = not isinstance(scraper, EthicalScraper)
    sitemap_scraper: EthicalScraper = (
        scraper  # type: ignore[assignment]
        if isinstance(scraper, EthicalScraper)
        else EthicalScraper(user_agent=settings.default_user_agent)
    )
    try:
        try:
            urls = await asyncio.to_thread(
                fetch_sitemap_urls, sitemap_url, link_pattern, sitemap_scraper
            )
        except Exception as e:
            logger.error("Failed to fetch sitemap %s: %s", sitemap_url, str(e))
            await _fail_job(db, job, f"Sitemap fetch failed: {e}")
            return

        if not urls:
            logger.warning("Sitemap returned 0 matching URLs for job %s", job_id)
            await _complete_job(db, job)
            return

        logger.info("Sitemap: %d property URLs to scrape for job %s", len(urls), job_id)

        listings_found = len(urls)
        listings_scraped = 0
        errors = 0
        warnings = 0
        new_count = 0
        updated_count = 0

        for url in urls:
            job.add_url("found", url)
        job.update_progress(
            pages_visited=1,
            listings_found=listings_found,
            listings_scraped=0,
            errors=0,
        )
        job.touch_heartbeat()
        await db.commit()

        job_cancelled = False
        for link in urls:
            if await _check_job_cancelled(db, job_id):
                logger.info("Job %s was cancelled", job_id)
                job_cancelled = True
                break

            scraped, errored, warned, is_new = await _process_listing_url(
                db, job, job_id, site_key, scraper, link, full_selectors, extraction_mode
            )
            if scraped:
                listings_scraped += 1
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1
            if errored:
                errors += 1
            if warned:
                warnings += 1
            if scraped or errored or warned:
                job.update_progress(
                    pages_visited=1,
                    listings_found=listings_found,
                    listings_scraped=listings_scraped,
                    errors=errors,
                    warnings=warnings,
                    new_listings=new_count,
                    updated_listings=updated_count,
                )
        deleted_count = 0
        if job_cancelled:
            logger.info(
                "Job %s: skipping auto-delete — job was cancelled before the crawl finished",
                job_id,
            )
            job.add_log(
                "info",
                "Auto-delete skipped: job was cancelled before the crawl finished",
            )
        else:
            deleted_count = await _remove_missing_listings(
                db=db,
                job=job,
                site_key=site_key,
                discovered_urls=set(urls),   # urls já é a lista completa do sitemap
            )
        if deleted_count:
            job.update_progress(
                pages_visited=1,
                listings_found=listings_found,
                listings_scraped=listings_scraped,
                errors=errors,
                warnings=warnings,
                new_listings=new_count,
                updated_listings=updated_count,
                deleted_listings=deleted_count,
            )
            await db.commit()
        await _complete_job(db, job)
    finally:
        if owned_sitemap_scraper:
            sitemap_scraper.close()


async def _process_listing_url(
    db: AsyncSession,
    job: ScrapeJob,
    job_id: str,
    site_key: str,
    scraper: "EthicalScraper | PlaywrightScraper",
    link: str,
    full_selectors: dict[str, Any],
    extraction_mode: str,
) -> tuple[bool, bool, bool, bool]:
    """Fetch, parse e persist um único URL de listing.

    Returns (scraped, errored, warned, is_new):
    - scraped=True  → listing persistido com sucesso.
    - errored=True  → excepção durante o processamento (parsing, DB, etc.).
    - warned=True   → HTML não disponível (fetch devolveu None — 404, timeout, bloqueio).

    Listings detetados como vendidos/reservados são ignorados (skip) e, se já
    existirem na DB de uma scrape anterior, são removidos.

    O caller é responsável por chamar job.update_progress() com os contadores actualizados.
    """
    try:
        # ── Skip URLs já confirmados como vendidos/reservados recentemente ──
        # Alguns sitemaps de parceiros nunca removem anúncios vendidos, pelo
        # que o mesmo URL morto voltaria a pagar o custo completo de fetch
        # (delay ético + render JS) em todos os scrapes seguintes.
        if await SoldListingRepository.is_recently_confirmed_sold(db, link, SOLD_URL_CACHE_TTL_DAYS):
            logger.info("Skipping recently-confirmed-sold URL (cached): %s", link)
            job.add_log("info", "Listing marked as sold/reserved — skipped (cached)", link)
            job.touch_heartbeat()
            await db.commit()
            return False, False, False, False
        # ─────────────────────────────────────────────────────────────────

        detail_html = await _fetch_html(scraper, link)
        if not detail_html and getattr(scraper, "last_status", None) in GONE_STATUSES:
            # 404/410: the ad was taken down at the source. Flag the stored
            # listing (if any) instead of treating this as a fetch failure, and
            # remember the URL so the next crawls don't pay to re-fetch it.
            existing = await ListingRepository.get_by_source_url(db, link)
            if existing and await ListingRepository.mark_removed(db, existing):
                logger.info("Listing gone at source (HTTP %s) — marked removed: %s", scraper.last_status, link)
            job.add_log("info", f"Listing gone at source (HTTP {scraper.last_status}) — skipped", link)
            await SoldListingRepository.mark_sold(db, site_key, link)
            job.touch_heartbeat()
            await db.commit()
            return False, False, False, False
        if not detail_html:
            job.add_url("failed", link)
            job.add_log("warning", "Failed to fetch listing page", link)
            job.touch_heartbeat()
            await db.commit()
            return False, False, True, False

        raw_data = parse_listing_page(detail_html, link, full_selectors, extraction_mode)
        # ── Skip listings vendidos/reservados ──────────────────────────────
        if raw_data.get("is_sold"):
            logger.info("Listing detected as sold/reserved — skipping: %s", link)
            job.add_log("info", "Listing marked as sold/reserved — skipped", link)

            existing = await ListingRepository.get_by_source_url(db, link)
            if existing and await ListingRepository.mark_removed(db, existing):
                logger.info("Marked existing listing removed (now sold): %s", link)

            await SoldListingRepository.mark_sold(db, site_key, link)

            job.touch_heartbeat()
            await db.commit()
            return False, False, False, False
        # ─────────────────────────────────────────────────────────────────

        # Listing is active — if it was previously cached as sold (relisted
        # after being taken off the market), drop the stale cache entry.
        await SoldListingRepository.unmark_sold(db, link)

        property_schema = normalize_partner_payload(raw_data, site_key)

        # Checked against the normalized schema, not raw_data — some partners
        # derive these fields purely in the mapper (URL parsing, fixed
        # constants) rather than via an HTML selector, so they'd never appear
        # in raw_data even when correctly populated in the final schema.
        if is_junk_listing(property_schema):
            # A shell page (error page, empty template) — never store it, and
            # never let it overwrite a good record from an earlier scrape.
            logger.warning("Skipping junk page (no real title/type/price): %s", link)
            job.add_log("warning", "Page has no listing data (numeric/empty title, no type, no price) — skipped", link)
            job.add_url("failed", link)
            job.touch_heartbeat()
            await db.commit()
            return False, False, True, False

        missing_fields = missing_critical_schema_fields(property_schema)
        if missing_fields:
            job.add_log(
                "warning",
                f"Critical parser fields missing: {', '.join(missing_fields)}",
                link,
            )

        is_new = await _persist_listing(db, job_id, property_schema, site_key)
        job.add_url("scraped", link)
        job.touch_heartbeat()
        await db.commit()
        return True, False, False, is_new

    except Exception as e:
        logger.exception("Error processing listing %s", link)
    
        await db.rollback()
        await db.refresh(job)
    
        job.add_url("failed", link)
        job.add_log(
            "error",
            f"Error processing listing: {type(e).__name__}: {e}",
            link,
        )
    
        job.touch_heartbeat()
        await db.commit()
    
        return False, True, False, False


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


async def _persist_listing(db: AsyncSession, job_id: str, schema, site_key: str) -> bool:
    """Persist a listing atomically, using PostgreSQL upsert when possible."""
    listing_data = schema_to_listing_dict(schema, scrape_job_id=UUID(job_id))
    source_url = listing_data.get("source_url")

    # SQLAlchemy 2.x: inspect engine dialect via the session's bind
    try:
        dialect_name = db.get_bind().dialect.name
    except Exception:
        dialect_name = engine.dialect.name

    if source_url and dialect_name == "postgresql":
        return await _persist_listing_with_postgres_upsert(db, job_id, schema, listing_data)

    return await _persist_listing_legacy(db, job_id, schema, listing_data)


def _hash_for_new(listing_data: dict[str, Any], schema) -> str:
    return compute_content_hash(listing_data, [str(m.url) for m in (getattr(schema, "media", None) or [])])


def _merged_content(existing: Listing, listing_data: dict[str, Any]) -> dict[str, Any]:
    """Content the row would hold after this scrape: new non-None values win.

    Mirrors the overwrite rule of the update loop (None never erases a stored
    value, except the amenity flags), so the hash reflects the *effective* state
    and a flaky extraction that drops a field does not register as a change.
    """
    merged = {f: getattr(existing, f) for f in CONTENT_FIELDS}
    for field, value in listing_data.items():
        if field in CONTENT_FIELDS and (value is not None or field in _FEATURE_FLAG_FIELDS):
            merged[field] = value
    return merged


async def _stored_media_urls(db: AsyncSession, listing_id: UUID) -> list[str]:
    rows = await db.execute(
        select(MediaAsset.url)
        .where(MediaAsset.listing_id == listing_id)
        .order_by(MediaAsset.position.asc().nulls_last(), MediaAsset.created_at.asc())
    )
    return list(rows.scalars().all())


async def _update_existing_listing(
    db: AsyncSession,
    job_id: str,
    existing: Listing,
    schema,
    listing_data: dict[str, Any],
) -> None:
    """Apply a re-scrape to a stored listing, moving ``updated_at`` only on real change.

    Every visit records ``last_seen_at``/``scrape_job_id``. ``updated_at`` (which
    the API exposes and the WordPress sync keys on) only moves when the content
    hash changes or the listing comes back from ``removed``.
    """
    source_url = listing_data.get("source_url")
    now = datetime.now(timezone.utc)
    new_media_urls = [str(m.url) for m in (getattr(schema, "media", None) or [])]

    merged = _merged_content(existing, listing_data)
    new_hash = compute_content_hash(merged, new_media_urls)
    # The "before" hash is recomputed from the stored row rather than read from
    # content_hash: other writers (PATCH, AI enrichment, rows predating the
    # column) change content without refreshing it, and trusting a stale hash
    # would hide a scrape that reverts their edit. One small SELECT per visit.
    old_hash = compute_content_hash(
        {f: getattr(existing, f) for f in CONTENT_FIELDS},
        await _stored_media_urls(db, existing.id),
    )
    reactivated = existing.status != STATUS_ACTIVE

    if new_hash == old_hash and not reactivated:
        # Core UPDATE with an explicit updated_at: the column's onupdate would
        # otherwise bump it for any write to the row.
        await db.execute(
            update(Listing)
            .where(Listing.id == existing.id)
            .values(
                last_seen_at=now,
                scrape_job_id=UUID(job_id),
                content_hash=new_hash,
                updated_at=Listing.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await _replace_media_assets(db, existing.id, schema)
        logger.debug("Listing unchanged: %s", source_url)
        return

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
        logger.info("Price change for %s: %s → %s", source_url, existing.price_amount, new_price)

    if reactivated:
        logger.info("Reactivating previously removed listing: %s", source_url)
        existing.status = STATUS_ACTIVE
        existing.removed_at = None

    logger.info("Updating existing listing: %s", source_url)
    for field, value in listing_data.items():
        if field == "scrape_job_id":
            continue
        if value is not None or field in _FEATURE_FLAG_FIELDS:
            setattr(existing, field, value)

    existing.content_hash = new_hash
    existing.updated_at = now
    existing.last_seen_at = now
    existing.scrape_job_id = UUID(job_id)

    await _replace_media_assets(db, existing.id, schema)


async def _persist_listing_with_postgres_upsert(
    db: AsyncSession,
    job_id: str,
    schema,
    listing_data: dict[str, Any],
) -> bool:
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
                .values(**listing_data, content_hash=_hash_for_new(listing_data, schema))
                .on_conflict_do_nothing(index_elements=[Listing.source_url])
                .returning(Listing.id)
            )
        ).scalar_one_or_none()

        if inserted_id is not None:
            await _replace_media_assets(db, inserted_id, schema)

            return True

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

    await _update_existing_listing(db, job_id, existing, schema, listing_data)
    return False


async def _persist_listing_legacy(
    db: AsyncSession,
    job_id: str,
    schema,
    listing_data: dict[str, Any],
) -> bool:
    """Fallback persistence path for non-PostgreSQL environments."""
    existing = None
    if listing_data.get("source_url"):
        result = await db.execute(
            select(Listing).where(Listing.source_url == listing_data["source_url"])
        )
        existing = result.scalar_one_or_none()

    if existing:
        await _update_existing_listing(db, job_id, existing, schema, listing_data)
        return False

    else:
        listing = Listing(**listing_data, content_hash=_hash_for_new(listing_data, schema))
        db.add(listing)
        await db.flush()  # Flush necessari per obtenir l'ID abans d'afegir fitxers multimèdia

        await _replace_media_assets(db, listing.id, schema)
        return True


async def _replace_media_assets(db: AsyncSession, listing_id: UUID, schema) -> None:
    """Replace listing media atomically so retries and upserts do not duplicate assets."""
    # 1. Clear out any old assets
    logger.warning("media assets found in schema : %s", schema.media)
    await db.execute(delete(MediaAsset).where(MediaAsset.listing_id == listing_id))

    # 2. Extract media safely (handling potential fallback names like 'images')
    media_list = getattr(schema, "media", None) or getattr(schema, "images", [])
    
    if not media_list:
        logger.warning("No media assets found in schema for listing ID: %s", listing_id)
        return

    for media in media_list:
        db.add(
            MediaAsset(
                listing_id=listing_id,
                url=str(media.url),
                alt_text=getattr(media, "alt_text", None),
                type=getattr(media, "type", "photo") or "photo",
                position=getattr(media, "position", 0),
            )
        )
    
    # 3. Force an immediate flush of the added media assets to the database transaction
    await db.flush()
    logger.info("Successfully flushed %d media assets for listing %s", len(media_list), listing_id)
async def _remove_missing_listings(
    db: AsyncSession,
    job: ScrapeJob,
    site_key: str,
    discovered_urls: set[str],
    *,
    min_discovered: int = 10,
    max_delete_ratio: float = 0.40,
) -> int:
    """Soft-delete (status=removed) active listings of this partner not seen in this crawl.

    Rows are kept so history survives and the listing is reactivated if it
    reappears; list endpoints hide them by default.

    Safeguards:
    - Aborts if discovered_urls is suspiciously small (failed crawl).
    - Aborts if the removal ratio exceeds max_delete_ratio (site restructure / bug).
    """
    if len(discovered_urls) < min_discovered:
        logger.warning(
            "Job %s: skipping removal — only %d URLs discovered (min: %d)",
            job.id, len(discovered_urls), min_discovered,
        )
        job.add_log(
            "warning",
            f"Auto-remove skipped: only {len(discovered_urls)} URLs discovered (min: {min_discovered})",
        )
        return 0

    total_active = await db.scalar(
        select(func.count()).where(Listing.source_partner == site_key, Listing.status == STATUS_ACTIVE)
    )

    if total_active and total_active > 0:
        to_remove_count = await db.scalar(
            select(func.count()).where(
                Listing.source_partner == site_key,
                Listing.status == STATUS_ACTIVE,
                Listing.source_url.notin_(discovered_urls),
            )
        )
        ratio = (to_remove_count or 0) / total_active
        if ratio > max_delete_ratio:
            logger.error(
                "Job %s: skipping removal — would remove %d/%d listings (%.0f%% > limit %.0f%%)",
                job.id, to_remove_count, total_active, ratio * 100, max_delete_ratio * 100,
            )
            job.add_log(
                "warning",
                f"Auto-remove skipped: {to_remove_count}/{total_active} listings would be removed "
                f"({ratio:.0%} exceeds {max_delete_ratio:.0%} safety limit)",
            )
            return 0

    removed_count = await ListingRepository.mark_missing_removed(db, site_key, discovered_urls)

    if removed_count:
        logger.info(
            "Job %s: marked %d stale listings removed for partner '%s'",
            job.id, removed_count, site_key,
        )
        job.add_log(
            "info",
            f"Marked {removed_count} listings removed (no longer present on site)",
        )

    return removed_count


async def _complete_job(db: AsyncSession, job: ScrapeJob) -> None:
    """Marca o job como completo."""
    if job and job.status == "running":
        if job.cancel_requested_at is not None:
            job.mark_cancelled()
            await record_event(db, job.id, "cancelled", "Scrape job cancelled")
            logger.info("Job %s cancelled successfully", job.id)
        else:
            await _update_site_confidence_scores(db, job.site_key, job.id)
            job.mark_completed()
            await record_event(db, job.id, "completed", "Scrape job completed", data=job.progress)
            logger.info("Job %s completed successfully", job.id)
            await send_job_notification(
                site_key=job.site_key,
                job_id=str(job.id),
                status="completed",
                progress=job.progress,
                started_at=job.started_at,
                finished_at=job.completed_at,
                start_url=job.start_url,
                warnings_count=(job.progress or {}).get("warnings", 0),
            )
        await db.commit()


async def _update_site_confidence_scores(db: AsyncSession, site_key: str, job_uuid: UUID) -> None:
    """Persist field extraction confidence back to the site configuration."""
    site = (
        await db.execute(select(SiteConfig).where(SiteConfig.key == site_key))
    ).scalar_one_or_none()
    if site is None:
        return

    listings = (
        await db.execute(
            select(Listing)
            .where(Listing.scrape_job_id == job_uuid)
            .options(selectinload(Listing.media_assets))
        )
    ).scalars().all()
    scores = calculate_confidence(listings, not_applicable_fields=site.confidence_not_applicable_fields)

    # Store field scores + metadata in the same JSON column.
    # _meta is stripped out in SiteConfigRead and exposed as confidence_meta.
    site.confidence_scores = {
        **scores,
        "_meta": {
            "job_id": str(job_uuid),
            "sample_count": len(listings),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    # flag_modified is required for SQLAlchemy to detect JSON column changes
    # even on full reassignment (avoids silent no-op commits).
    flag_modified(site, "confidence_scores")
    log_low_confidence_scores(site_key, scores)


async def _fail_job(db: AsyncSession, job: ScrapeJob, error: str) -> None:
    """Marca o job como falhado."""
    await db.rollback()
    if job:
        await db.refresh(job)
        if job.status == "running":
            job.mark_failed(error)
            await record_event(db, job.id, "failed", error, level="error")
            await db.commit()
            logger.error("Job %s failed: %s", job.id, error)
            await send_job_notification(
                site_key=job.site_key,
                job_id=str(job.id),
                status="failed",
                progress=job.progress,
                error_message=error,
                started_at=job.started_at,
                finished_at=job.completed_at,
                start_url=job.start_url,
                warnings_count=(job.progress or {}).get("warnings", 0),
            )
