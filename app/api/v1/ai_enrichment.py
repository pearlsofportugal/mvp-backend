"""AI enrichment API router — multi-locale SEO content generation.
/api/v1/enrichment/ai"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ok, ERROR_RESPONSES
from app.schemas.ai_enrichment_schema import (
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    EnrichmentStats,
    ListingTranslationRequest,
    ListingTranslationResponse,
)
from app.schemas.base_schema import ApiResponse
from app.services.ai_enrichment_service import (
    bulk_enrich_listings,
    enrich_translations_and_persist,
    get_enrichment_stats,
    get_listings_for_bulk_enrich,
)

router = APIRouter()


@router.get("/stats", response_model=ApiResponse[EnrichmentStats], responses=ERROR_RESPONSES, operation_id="enrichment_stats")
async def enrichment_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: str | None = Query(None, description="Filter stats by source partner"),
):
    """Aggregated enrichment statistics across all listings."""
    stats = await get_enrichment_stats(db, source_partner)
    return ok(stats, "Enrichment stats retrieved successfully", request)


@router.post("/bulk", response_model=ApiResponse[BulkEnrichmentResponse], responses=ERROR_RESPONSES, operation_id="bulk_enrich_listings")
async def bulk_enrich(
    payload: BulkEnrichmentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Enrich multiple listings in one call using the multi-locale translations endpoint.

    When ``listing_ids`` is provided, only those listings are processed.
    Otherwise, all unenriched listings (optionally filtered by ``source_partner``)
    are queued up to ``limit``.
    """
    listings = await get_listings_for_bulk_enrich(db, payload)
    response = await bulk_enrich_listings(list(listings), payload)
    await db.commit()
    return ok(response, f"{response.enriched} listing(s) enriched, {response.skipped} skipped, {response.failed} failed", request)


@router.post(
    "/translations",
    response_model=ApiResponse[ListingTranslationResponse],
    responses=ERROR_RESPONSES,
    operation_id="translate_listing",
)
async def translate_listing(
    payload: ListingTranslationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate multi-locale SEO content (EN, PT, ES, FR, DE) from original scraped data.

    All locales are generated independently in a single AI call — no chaining between languages.

    - **apply=False** (default): AI is called for locales that do not already have stored content.
      Returns a preview without writing to the database.
    - **apply=True**: Persists the ``translation_values`` provided by the caller.
      AI is NOT called in this path — supply the output from a prior apply=False call.
    - **force=True**: Regenerates locales even if they already have stored translations.
    """
    response = await enrich_translations_and_persist(db, payload.listing_id, payload)
    message = (
        f"Translations applied for locales: {', '.join(response.locales_generated)}"
        if response.applied
        else f"Translations generated for: {', '.join(response.locales_generated) or 'none (all cached)'}"
    )
    return ok(response, message, request)