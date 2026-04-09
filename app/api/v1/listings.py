"""Listings API router — full CRUD with filtering, pagination, sorting, stats.
/api/v1/listings"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.schemas.base_schema import ApiResponse
from app.schemas.listing_schema import (
    DuplicatesResponse,
    ListingCreate,
    ListingDetailRead,
    ListingStats,
    ListingUpdate,
    PaginatedResponse,
)
from app.schemas.listing_search_schema import ListingSearchResponse
from app.services.listing_service import SORT_FIELDS, ListingService

router = APIRouter()


@router.get(
    "",
    response_model=ApiResponse[PaginatedResponse],
    responses=ERROR_RESPONSES,
    operation_id="list_listings",
)
async def list_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    district: str | None = Query(None),
    county: str | None = Query(None),
    parish: str | None = Query(None),
    property_type: str | None = Query(None),
    typology: str | None = Query(None),
    listing_type: str | None = Query(None, pattern="^(sale|rent)$"),
    source_partner: str | None = Query(None),
    scrape_job_id: UUID | None = Query(None),
    price_min: Decimal | None = Query(None),
    price_max: Decimal | None = Query(None),
    area_min: float | None = Query(None),
    area_max: float | None = Query(None),
    bedrooms_min: int | None = Query(None),
    bedrooms_max: int | None = Query(None),
    has_garage: bool | None = Query(None),
    has_pool: bool | None = Query(None),
    has_elevator: bool | None = Query(None),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("created_at", enum=list(SORT_FIELDS.keys())),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List listings with filtering, sorting, and pagination."""
    filter_kwargs = {
        "district": district, "county": county, "parish": parish,
        "property_type": property_type, "typology": typology, "listing_type": listing_type,
        "source_partner": source_partner, "scrape_job_id": scrape_job_id,
        "price_min": price_min, "price_max": price_max,
        "area_min": area_min, "area_max": area_max,
        "bedrooms_min": bedrooms_min, "bedrooms_max": bedrooms_max,
        "has_garage": has_garage, "has_pool": has_pool, "has_elevator": has_elevator,
        "created_after": created_after, "created_before": created_before,
        "search": search,
    }
    paginated, meta = await ListingService.get_all_listings(db, filter_kwargs, sort_by, sort_order, page, page_size)
    return ok(paginated, "Listings listed successfully", request, meta=meta)


@router.get(
    "/selector",
    response_model=ApiResponse[ListingSearchResponse],
    responses=ERROR_RESPONSES,
    operation_id="selector_listings",
)
async def selector_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(None),
    source_partner: str | None = Query(None),
    is_enriched: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Lightweight listing picker for the selector UI."""
    results, meta = await ListingService.search_listings(db, q, source_partner, is_enriched, page, page_size)
    return ok(results, "Listings found", request, meta=meta)


@router.get(
    "/stats",
    response_model=ApiResponse[ListingStats],
    responses=ERROR_RESPONSES,
    operation_id="listing_stats",
)
async def listing_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: str | None = Query(None),
    scrape_job_id: UUID | None = Query(None),
):
    """Aggregated listing statistics."""
    stats = await ListingService.get_stats(db, source_partner, scrape_job_id)
    return ok(stats, "Listing stats retrieved successfully", request)


@router.get(
    "/duplicates",
    response_model=ApiResponse[DuplicatesResponse],
    responses=ERROR_RESPONSES,
    operation_id="detect_duplicates",
)
async def detect_duplicates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Detect duplicate listings by source_url."""
    duplicates, meta = await ListingService.get_duplicates(db, page, page_size)
    return ok(duplicates, "Duplicates detected successfully", request, meta=meta)


# ════════════════════════════════════════════════════════════════
#  DYNAMIC ROUTES — /{listing_id} must come LAST
# ════════════════════════════════════════════════════════════════
@router.get(
    "/{listing_id}",
    response_model=ApiResponse[ListingDetailRead],
    responses=ERROR_RESPONSES,
    operation_id="get_listing",
)
async def get_listing(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a single listing by ID."""
    listing = await ListingService.get_listing_by_id(db, listing_id)
    return ok(ListingDetailRead.model_validate(listing), "Listing retrieved successfully", request)


@router.post(
    "",
    response_model=ApiResponse[ListingDetailRead],
    status_code=201,
    responses={**ERROR_RESPONSES, 409: {"model": ApiResponse, "description": "Listing with this source_url already exists."}},
    operation_id="create_listing",
)
async def create_listing(
    payload: ListingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new listing manually."""
    listing = await ListingService.create_listing(db, payload)
    return ok(ListingDetailRead.model_validate(listing), "Listing created successfully", request)


@router.patch(
    "/{listing_id}",
    response_model=ApiResponse[ListingDetailRead],
    responses=ERROR_RESPONSES,
    operation_id="update_listing",
)
async def update_listing(
    listing_id: UUID,
    payload: ListingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a listing."""
    listing = await ListingService.update_listing(db, listing_id, payload)
    return ok(ListingDetailRead.model_validate(listing), "Listing updated successfully", request)


@router.delete(
    "/{listing_id}",
    response_model=ApiResponse[None],
    status_code=200,
    responses=ERROR_RESPONSES,
    operation_id="delete_listing",
)
async def delete_listing(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a listing (hard delete — cascades to media_assets and price_history)."""
    await ListingService.delete_listing(db, listing_id)
    return ok(None, "Listing deleted successfully", request)
