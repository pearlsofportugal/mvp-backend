"""AI enrichment API router — optimize text and selected listing fields with AI.
/api/v1/enrichment/ai"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ok, ERROR_RESPONSES
from app.schemas.ai_enrichment_schema import (
    AIEnrichmentOutput,
    AIListingEnrichmentRequest,
    AIListingEnrichmentResponse,
    AITextOptimizationRequest,
    AITextOptimizationResponse,
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    EnrichmentStats,
)
from app.schemas.base_schema import ApiResponse
from app.services.ai_enrichment_service import (
    bulk_enrich_listings,
    enrich_and_persist,
    get_enrichment_stats,
    get_listings_for_bulk_enrich,
    optimize_text_with_ai,
)

router = APIRouter()


@router.post("/optimize", response_model=ApiResponse[AITextOptimizationResponse], responses=ERROR_RESPONSES, operation_id="optimize_text")
async def optimize_text(payload: AITextOptimizationRequest, request: Request):
    """Optimize free text with AI SEO logic."""
    result = await asyncio.to_thread(optimize_text_with_ai, payload.content, payload.keywords)
    if payload.fields:
        filtered = result.output.model_dump(include=set(payload.fields))
        result.output = AIEnrichmentOutput.model_validate(filtered)
    return ok(result, "Text optimized successfully", request)


@router.post("/enhance", response_model=ApiResponse[AIListingEnrichmentResponse], responses=ERROR_RESPONSES, operation_id="enhance_listing")
async def enhance_listing(
    payload: AIListingEnrichmentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Enrich selected listing fields (title/description/meta_description) using AI."""
    response = await enrich_and_persist(db, payload.listing_id, payload)
    return ok(response, "Listing enriched successfully", request)


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
    """Enrich multiple listings in one call.

    When ``listing_ids`` is provided, only those listings are processed.
    Otherwise, all unenriched listings (optionally filtered by ``source_partner``)
    are queued up to ``limit``.
    """
    listings = await get_listings_for_bulk_enrich(db, payload)
    response = await bulk_enrich_listings(list(listings), payload)
    await db.commit()
    return ok(response, f"{response.enriched} listing(s) enriched, {response.skipped} skipped, {response.failed} failed", request)