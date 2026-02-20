"""AI enrichment API router — optimize text and selected listing fields with AI.
/api/v1/enrichment/ai"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ok
from app.core.exceptions import NotFoundError
from app.models.listing import Listing
from app.schemas.ai_enrichment import (
    AIEnrichmentOutput,
    AIListingEnrichmentRequest,
    AIListingEnrichmentResponse,
    AITextOptimizationRequest,
    AITextOptimizationResponse,
    EnrichmentPreview,
    EnrichmentSourceStats,
    EnrichmentStats,
)
from app.schemas.base_schema import ApiResponse
from app.services.ai_enrichment_service import enrich_listing_with_ai, optimize_text_with_ai
import asyncio
router = APIRouter()


@router.post("/optimize", response_model=ApiResponse[AITextOptimizationResponse])
async def optimize_text(payload: AITextOptimizationRequest, request: Request):
    """Optimize free text with AI SEO logic."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, optimize_text_with_ai, payload.content, payload.keywords)
    if payload.fields:
        filtered = result.output.model_dump(include=set(payload.fields))
        result.output = AIEnrichmentOutput.model_validate(filtered)
    return ok(result, "Text optimized successfully", request)


@router.post("/listing", response_model=ApiResponse[AIListingEnrichmentResponse])
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


@router.get("/preview/{listing_id}", response_model=ApiResponse[EnrichmentPreview])
async def preview_enrichment(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Preview AI-generated description for a listing without persisting changes."""
    listing = (
        await db.execute(select(Listing).where(Listing.id == listing_id))
    ).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    # Reutiliza a lógica de /listing com apply=False e force=True
    preview_request = AIListingEnrichmentRequest(
        listing_id=listing_id,
        fields=["description"],
        apply=False,
        force=True,
    )
    response = await enrich_listing_with_ai(listing, preview_request)

    desc_result = next(
        (r for r in response.results if r.field == "description"), None
    )

    return ok(
        EnrichmentPreview(
            original_description=desc_result.original if desc_result else None,
            enriched_description=desc_result.enriched if desc_result else None,
            model_used=response.model_used,
        ),
        "Preview generated successfully",
        request,
    )


@router.get("/stats", response_model=ApiResponse[EnrichmentStats])
async def enrichment_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: Optional[str] = Query(None, description="Filter stats by source partner"),
):
    """Aggregated enrichment statistics across all listings."""
    # Total de listings (com filtro opcional por source_partner)
    total_query = select(func.count(Listing.id))
    enriched_query = select(func.count(Listing.id)).where(
        Listing.enriched_description.isnot(None)
    )

    if source_partner:
        total_query = total_query.where(Listing.source_partner == source_partner)
        enriched_query = enriched_query.where(Listing.source_partner == source_partner)

    total: int = (await db.execute(total_query)).scalar_one()
    enriched: int = (await db.execute(enriched_query)).scalar_one()

    # Breakdown por source_partner
    by_source_query = select(
        Listing.source_partner,
        func.count(Listing.id).label("total"),
        func.count(Listing.enriched_description).label("enriched"),
    ).group_by(Listing.source_partner)

    if source_partner:
        by_source_query = by_source_query.where(Listing.source_partner == source_partner)

    by_source_rows = (await db.execute(by_source_query)).all()

    by_source = {
        row.source_partner: EnrichmentSourceStats(
            total=row.total,
            enriched=row.enriched,
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