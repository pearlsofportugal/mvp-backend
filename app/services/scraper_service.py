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

MELHORIAS v2:
- Eliminação de N+1 queries: funções auxiliares recebem o objeto `job` diretamente
- Commits em batch por listing (em vez de um commit por operação)
- `print()` de debug substituído por `logger.debug()`
- Import duplicado de `schema_to_listing_dict` em `_persist_listing` removido
- Media assets também são atualizados (upsert por URL) em listings existentes
"""
import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional
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

    Abre uma única sessão de DB para toda a duração do job — todas as funções
    auxiliares recebem o objeto `job` como argumento para evitar N+1 queries.
    """
    set_correlation_id(job_id)
    logger.info("Starting scrape job %s", job_id)

    async with async_session_factory() as db:
        try:
            result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == UUID(job_id)))
            job = result.scalar_one_or_none()
            if not job:
                logger.error("Job %s not found", job_id)
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

            logger.debug(
                "pagination_type=%s, pagination_param=%s, site_config_id=%s",
                site_config.pagination_type,
                site_config.pagination_param,
                site_config.id,
            )

            await _run_scrape_async(
                db=db,
                job=job,
                site_key=job.site_key,
                base_url=site_config.base_url,
                start_url=job.start_url,
                max_pages=job.max_pages,
                selectors=site_config.selectors,
                extraction_mode=site_config.extraction_mode,
                link_pattern=site_config.link_pattern,
                image_filter=site_config.image_filter,
                config=job.config or {},
                pagination_type=site_config.pagination_type,
                pagination_param=site_config.pagination_param,
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
    db: AsyncSession,
    job: ScrapeJob,
    site_key: str,
    base_url: str,
    start_url: str,
    max_pages: int,
    selectors: Dict[str, Any],
    extraction_mode: str,
    link_pattern: Optional[str],
    image_filter: Optional[str],
    config: Dict[str, Any],
    pagination_type: str = "html_next",
    pagination_param: Optional[str] = None,
) -> None:
    """Async scraping loop — recebe a sessão DB e o objeto job existentes."""
    scraper = EthicalScraper(
        min_delay=config.get("min_delay") or settings.default_min_delay,
        max_delay=config.get("max_delay") or settings.default_max_delay,
        user_agent=config.get("user_agent") or settings.default_user_agent,
        timeout=settings.request_timeout,
    )

    try:
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
            if _is_cancelled(job):
                logger.info("Job %s was cancelled", job.id)
                break

            logger.info("Scraping page %d: %s", page_num + 1, current_url)

            response = await asyncio.to_thread(scraper.get, current_url)
            if not response:
                logger.warning("Failed to fetch page: %s", current_url)
                job.add_log("error", f"Failed to fetch page: {current_url}", current_url)
                await db.commit()
                errors += 1
                break

            pages_visited += 1
            html = response.text

            links = parse_listing_links(html, base_url, full_selectors)
            listings_found += len(links)

            # Batch: registar todas as URLs da página num único commit
            for link in links:
                job.add_url("found", link)
            await db.commit()

            for link in links:
                if _is_cancelled(job):
                    break

                try:
                    detail_response = await asyncio.to_thread(scraper.get, link)
                    if not detail_response:
                        job.add_url("failed", link)
                        job.add_log("warning", "Failed to fetch listing page", link)
                        await db.commit()
                        continue

                    raw_data = await parse_listing_page(
                        detail_response.text,
                        link,
                        full_selectors,
                        extraction_mode,
                    )

                    property_schema = normalize_partner_payload(raw_data, site_key)
                    await _persist_listing(db, job, property_schema, site_key)

                    # Batch: track URL + progresso num único commit (já feito em _persist_listing)
                    job.add_url("scraped", link)
                    listings_scraped += 1
                    job.update_progress(
                        pages_visited=pages_visited,
                        listings_found=listings_found,
                        listings_scraped=listings_scraped,
                        errors=errors,
                    )
                    await db.commit()

                except Exception as e:
                    logger.error("Error processing listing %s: %s", link, str(e))
                    job.add_url("failed", link)
                    job.add_log("error", f"Error processing listing: {str(e)}", link)
                    errors += 1
                    await db.commit()

            # ---------- PAGINATION ----------
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
                break

        # Atualizar progresso final antes de completar
        job.update_progress(
            pages_visited=pages_visited,
            listings_found=listings_found,
            listings_scraped=listings_scraped,
            errors=errors,
        )
        if job.status == "running":
            job.mark_completed()
            await db.commit()
            logger.info("Job %s completed successfully", job.id)

    except Exception as e:
        logger.error("Scraping error: %s\n%s", str(e), traceback.format_exc())
        if job.status == "running":
            job.mark_failed(str(e))
            await db.commit()
    finally:
        scraper.close()


# ---------------------------------------------------------------------------
# Helpers — operam diretamente sobre o objeto `job` (sem re-fetch à DB)
# ---------------------------------------------------------------------------

def _is_cancelled(job: ScrapeJob) -> bool:
    """Verifica se o job foi cancelado — lê o estado em memória, sem query à DB.

    NOTA: O status é atualizado pelo endpoint de cancelamento via a mesma sessão,
    por isso a leitura em memória é suficiente para deteção atempada.
    """
    return job.status == "cancelled"


async def _persist_listing(db: AsyncSession, job: ScrapeJob, schema, site_key: str) -> None:
    """Persiste um listing na DB com deduplicação por source_url.

    - Novo listing: cria o registo + todos os media assets.
    - Listing existente: atualiza campos + faz upsert de media assets por URL
      (adiciona novos, mantém existentes, não remove os que desapareceram).

    Não faz commit — o caller é responsável pelo commit para permitir batching.
    """
    listing_data = schema_to_listing_dict(schema, scrape_job_id=UUID(str(job.id)))

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
        existing.scrape_job_id = UUID(str(job.id))

        # Upsert de media assets: adicionar apenas os que ainda não existem (por URL)
        if schema.media:
            existing_media_result = await db.execute(
                select(MediaAsset.url).where(MediaAsset.listing_id == existing.id)
            )
            existing_urls = {row[0] for row in existing_media_result.fetchall()}

            for media in schema.media:
                media_url = str(media.url)
                if media_url not in existing_urls:
                    asset = MediaAsset(
                        listing_id=existing.id,
                        url=media_url,
                        alt_text=media.alt_text,
                        type=media.type or "photo",
                    )
                    db.add(asset)
                    logger.debug("New media asset for existing listing: %s", media_url)

    else:
        listing = Listing(**listing_data)
        db.add(listing)
        await db.flush()  # obter o ID antes de adicionar media assets

        for media in schema.media:
            asset = MediaAsset(
                listing_id=listing.id,
                url=str(media.url),
                alt_text=media.alt_text,
                type=media.type or "photo",
            )
            db.add(asset)