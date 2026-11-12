"""Cloud Run Job entrypoint: AI SEO enrichment (multi-locale) outside the API service.

Selects listings that still have no translations (or all of them with --force), enriches a few at a
time and commits each listing on its own, so an execution can be stopped, retried or re-run and it
simply continues with whatever is still pending.

    python -m app.enrich_worker                          # every pending listing
    python -m app.enrich_worker --partner realkey        # one site (comma-separated for several)
    python -m app.enrich_worker --force --partner habita # regenerate even the enriched ones
"""
import argparse
import asyncio
import sys
from typing import Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.exceptions import EnrichmentError
from app.core.logging import get_logger
from app.models.listing_model import Listing
from app.schemas.ai_enrichment_schema import _ALL_LOCALES, ListingTranslationRequest
from app.services.ai_enrichment_service import enrich_listing_translations

logger = get_logger(__name__)

CHUNK = 50  # listings selected per query
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_PAUSE_SECONDS = 20


async def pending_ids(db, partners: Sequence[str], force: bool, skip: set[UUID], limit: int) -> list[UUID]:
    """Listings still to process, oldest first, never one already handled in this run."""
    stmt = select(Listing.id)
    if not force:
        stmt = stmt.where(Listing.enriched_translations.is_(None))
    if partners:
        stmt = stmt.where(Listing.source_partner.in_(partners))
    if skip:
        stmt = stmt.where(Listing.id.not_in(skip))
    stmt = stmt.order_by(Listing.created_at.asc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def enrich_one(listing_id: UUID, locales: list[str], force: bool) -> str:
    """'enriched', 'skipped' or 'failed' for one listing. The Gemini rate limiter rejects instead of
    waiting, so a rejected call is retried after a pause."""
    from app.database import async_session_factory
    from app.repositories.listings_repository import ListingRepository

    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        try:
            async with async_session_factory() as db:
                listing = await ListingRepository.get_listing_by_id(db, listing_id)
                if listing is None:
                    return "skipped"
                preview = await enrich_listing_translations(
                    listing, ListingTranslationRequest(listing_id=listing_id, locales=locales, apply=False, force=force)
                )
                if not preview.locales_generated:
                    return "skipped"
                await enrich_listing_translations(
                    listing,
                    ListingTranslationRequest(listing_id=listing_id, locales=locales, apply=True,
                                              translation_values=preview.results),
                )
                await db.commit()
                return "enriched"
        except EnrichmentError as exc:
            if "rate limit" in str(exc).lower() and attempt < RATE_LIMIT_RETRIES:
                await asyncio.sleep(RATE_LIMIT_PAUSE_SECONDS)
                continue
            logger.warning("enrichment failed for %s: %s", listing_id, exc)
            return "failed"
        except Exception as exc:  # one bad listing must not stop the run
            logger.warning("enrichment failed for %s: %s", listing_id, exc)
            return "failed"
    return "failed"


async def run(partners: Sequence[str], locales: list[str], force: bool, concurrency: int, limit: int) -> dict[str, int]:
    from app.database import async_session_factory

    counts = {"enriched": 0, "skipped": 0, "failed": 0}
    seen: set[UUID] = set()
    gate = asyncio.Semaphore(concurrency)

    async def worker(lid: UUID) -> None:
        async with gate:
            counts[await enrich_one(lid, locales, force)] += 1

    while limit <= 0 or len(seen) < limit:
        room = CHUNK if limit <= 0 else min(CHUNK, limit - len(seen))
        async with async_session_factory() as db:
            ids = await pending_ids(db, partners, force, seen, room)
        if not ids:
            break
        seen.update(ids)
        await asyncio.gather(*(worker(lid) for lid in ids))
        print(f"processed={len(seen)} enriched={counts['enriched']} skipped={counts['skipped']} "
              f"failed={counts['failed']}", flush=True)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI SEO enrichment over pending listings")
    parser.add_argument("--partner", default="", help="comma-separated source partners (default: all)")
    parser.add_argument("--locales", default=",".join(_ALL_LOCALES), help="comma-separated locales")
    parser.add_argument("--force", action="store_true", help="regenerate listings that are already enriched too")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="listings enriched at the same time (the API-wide limit is 20 Gemini calls/minute)")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many listings (0 = no limit)")
    args = parser.parse_args()

    partners = [p.strip() for p in args.partner.split(",") if p.strip()]
    locales = [loc.strip() for loc in args.locales.split(",") if loc.strip()]
    counts = asyncio.run(run(partners, locales, args.force, max(1, args.concurrency), args.limit))
    print(f"done: {counts}", flush=True)
    if counts["failed"] and not counts["enriched"]:
        sys.exit(1)  # nothing worked: let Cloud Run mark the execution as failed


if __name__ == "__main__":
    main()
