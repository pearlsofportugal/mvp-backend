"""AI enrichment API router — optimize text and selected listing fields with AI.
/api/v1/enrichment/ai"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ok, ERROR_RESPONSES
from app.core.exceptions import NotFoundError
from app.models.listing_model import Listing
from app.schemas.ai_enrichment_schema import (
    AIEnrichmentOutput,
    AIListingEnrichmentRequest,
    AIListingEnrichmentResponse,
    AITextOptimizationRequest,
    AITextOptimizationResponse,
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    EnrichmentPreview,
    EnrichmentSourceStats,
    EnrichmentStats,
)
from app.schemas.base_schema import ApiResponse
from app.services.ai_enrichment_service import bulk_enrich_listings, enrich_listing_with_ai, optimize_text_with_ai
import asyncio
router = APIRouter()


@router.post("/optimize", response_model=ApiResponse[AITextOptimizationResponse], responses=ERROR_RESPONSES, operation_id="optimize_text")
async def optimize_text(payload: AITextOptimizationRequest, request: Request):
    """Optimize free text with AI SEO logic."""
    result = await asyncio.to_thread(optimize_text_with_ai, payload.content, payload.keywords)
    if payload.fields:
        filtered = result.output.model_dump(include=set(payload.fields))
        result.output = AIEnrichmentOutput.model_validate(filtered)
    return ok(result, "Text optimized successfully", request)


@router.post("/listing", response_model=ApiResponse[AIListingEnrichmentResponse], responses=ERROR_RESPONSES, operation_id="enrich_listing")
async def enrich_listing(
    payload: AIListingEnrichmentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Enrich selected listing fields (title/description/meta_description) using AI."""
    listing = (
        await db.execute(select(Listing).where(Listing.id == payload.listing_id))
    ).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {payload.listing_id} not found")

    response = await enrich_listing_with_ai(listing, payload)
    if payload.apply:
        await db.commit()
    return ok(response, "Listing enriched successfully", request)


@router.get("/preview/{listing_id}", response_model=ApiResponse[EnrichmentPreview], responses=ERROR_RESPONSES, operation_id="preview_enrichment")
async def preview_enrichment(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Preview AI-generated SEO content for a listing without persisting changes."""
    listing = (
        await db.execute(select(Listing).where(Listing.id == listing_id))
    ).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    preview_request = AIListingEnrichmentRequest(
        listing_id=listing_id,
        fields=["title", "description", "meta_description"],
        apply=False,
        force=True,
    )
    response = await enrich_listing_with_ai(listing, preview_request)

    def _get(field: str) -> tuple[str | None, str | None]:
        result = next((r for r in response.results if r.field == field), None)
        return (result.original if result else None, result.enriched if result else None)

    orig_title, enr_title = _get("title")
    orig_desc, enr_desc = _get("description")
    orig_meta, enr_meta = _get("meta_description")

    return ok(
        EnrichmentPreview(
            original_title=orig_title,
            enriched_title=enr_title,
            original_description=orig_desc,
            enriched_description=enr_desc,
            original_meta_description=orig_meta,
            enriched_meta_description=enr_meta,
            model_used=response.model_used,
        ),
        "Preview generated successfully",
        request,
    )


@router.get("/stats", response_model=ApiResponse[EnrichmentStats], responses=ERROR_RESPONSES, operation_id="enrichment_stats")
async def enrichment_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: str | None = Query(None, description="Filter stats by source partner"),
):
    """Aggregated enrichment statistics across all listings."""
    # A listing is considered enriched when any of the three AI fields is populated.
    total_query = select(func.count(Listing.id))
    enriched_query = select(func.count(Listing.id)).where(
        (Listing.enriched_title.isnot(None))
        | (Listing.enriched_description.isnot(None))
        | (Listing.enriched_meta_description.isnot(None))
    )

    if source_partner:
        total_query = total_query.where(Listing.source_partner == source_partner)
        enriched_query = enriched_query.where(Listing.source_partner == source_partner)

    total: int = (await db.execute(total_query)).scalar_one()
    enriched: int = (await db.execute(enriched_query)).scalar_one()

    # Breakdown por source_partner
    _any_enriched = (
        (Listing.enriched_title.isnot(None))
        | (Listing.enriched_description.isnot(None))
        | (Listing.enriched_meta_description.isnot(None))
    )
    by_source_query = select(
        Listing.source_partner,
        func.count(Listing.id).label("total"),
        func.count(case((_any_enriched, 1))).label("enriched_count"),
    ).group_by(Listing.source_partner)

    if source_partner:
        by_source_query = by_source_query.where(Listing.source_partner == source_partner)

    by_source_rows = (await db.execute(by_source_query)).all()

    by_source = {
        row.source_partner: EnrichmentSourceStats(
            total=row.total,
            enriched_count=row.enriched_count,
        )
        for row in by_source_rows
        if row.source_partner is not None
    }

    return ok(
        EnrichmentStats(
            total_listings=total,
            enriched_count=enriched,
            not_enriched_count=total - enriched,
            enrichment_percentage=round((enriched / total * 100), 2) if total > 0 else 0.0,
            by_source=by_source,
        ),
        "Enrichment stats retrieved successfully",
        request,
    )


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
    if payload.listing_ids:
        stmt = select(Listing).where(Listing.id.in_(payload.listing_ids))
    else:
        unenriched_filter = (
            Listing.enriched_title.is_(None)
            & Listing.enriched_description.is_(None)
            & Listing.enriched_meta_description.is_(None)
        )
        stmt = select(Listing).where(unenriched_filter)
        if payload.source_partner:
            stmt = stmt.where(Listing.source_partner == payload.source_partner)
        stmt = stmt.order_by(Listing.created_at.asc()).limit(payload.limit)

    listings = (await db.execute(stmt)).scalars().all()

    response = await bulk_enrich_listings(list(listings), payload)
    await db.commit()
    return ok(response, f"{response.enriched} listing(s) enriched, {response.skipped} skipped, {response.failed} failed", request)