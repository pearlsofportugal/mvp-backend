"""Listings API router — full CRUD with filtering, pagination, sorting, stats.
/api/v1/listings"""
from decimal import Decimal
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
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
from app.api.responses import ok, ERROR_RESPONSES
from app.services.listing_service import ListingService, SORT_FIELDS

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
    "/search",
    response_model=ApiResponse[ListingSearchResponse],
    responses=ERROR_RESPONSES,
    operation_id="search_listings",
)
async def search_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(None),
    source_partner: str | None = Query(None),
    is_enriched: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Lightweight search endpoint for the listing selector UI."""
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

@router.get("/search", response_model=ApiResponse[ListingSearchResponse], responses=ERROR_RESPONSES, operation_id="search_listings")
async def search_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(None),
    source_partner: str | None = Query(None),
    is_enriched: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Lightweight search endpoint for the listing selector UI.

    Returns thumbnail_url (first media asset) and is_enriched flag.
    """
    stmt = select(Listing)

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Listing.title.ilike(pattern),
                Listing.district.ilike(pattern),
                Listing.county.ilike(pattern),
                Listing.source_partner.ilike(pattern),
            )
        )

    if source_partner:
        stmt = stmt.where(Listing.source_partner == source_partner)

    if is_enriched is True:
        stmt = stmt.where(Listing.enriched_description.isnot(None))
    elif is_enriched is False:
        stmt = stmt.where(Listing.enriched_description.is_(None))

    total: int = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()

    stmt = (
        stmt
        .options(selectinload(Listing.media_assets))
        .order_by(Listing.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    listings = (await db.execute(stmt)).scalars().all()

    items: list[ListingSearchItem] = []
    for listing in listings:
        thumbnail_url: str | None = None
        if listing.media_assets:
            first = min(listing.media_assets, key=lambda m: m.position or 999)
            thumbnail_url = first.url

        items.append(
            ListingSearchItem(
                id=listing.id,
                source_partner=listing.source_partner,
                title=listing.enriched_title or listing.title,
                property_type=listing.property_type,
                typology=listing.typology,
                bedrooms=listing.bedrooms,
                area_useful_m2=listing.area_useful_m2,
                district=listing.district,
                county=listing.county,
                price_amount=listing.price_amount,
                price_currency=listing.price_currency,
                listing_type=listing.listing_type,
                thumbnail_url=thumbnail_url,
                is_enriched=bool(
                    listing.enriched_title
                    or listing.enriched_description
                    or listing.enriched_meta_description
                ),
            )
        )

    pages = math.ceil(total / page_size) if total > 0 else 0
    return ok(
        ListingSearchResponse(items=items),
        "Listings found",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )
@router.get("/stats", response_model=ApiResponse[ListingStats], responses=ERROR_RESPONSES, operation_id="listing_stats")
async def listing_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: str | None = Query(None),
    scrape_job_id: UUID | None = Query(None),
):
    """Aggregated listing statistics."""
    base_filter = []
    if source_partner:
        base_filter.append(Listing.source_partner == source_partner)
    if scrape_job_id:
        base_filter.append(Listing.scrape_job_id == scrape_job_id)
    where_clause = and_(*base_filter) if base_filter else True

    q = select(
        func.count(Listing.id),
        func.avg(Listing.price_amount),
        func.min(Listing.price_amount),
        func.max(Listing.price_amount),
        func.avg(Listing.area_useful_m2),
    ).where(where_clause)
    row = (await db.execute(q)).one()
    total, avg_price, min_price, max_price, avg_area = row

    q_district = (
        select(Listing.district, func.count(Listing.id))
        .where(where_clause).where(Listing.district.isnot(None))
        .group_by(Listing.district)
    )
    by_district = {r[0]: r[1] for r in (await db.execute(q_district)).all()}

    q_type = (
        select(Listing.property_type, func.count(Listing.id))
        .where(where_clause).where(Listing.property_type.isnot(None))
        .group_by(Listing.property_type)
    )
    by_type = {r[0]: r[1] for r in (await db.execute(q_type)).all()}

    q_partner = (
        select(Listing.source_partner, func.count(Listing.id))
        .where(where_clause).group_by(Listing.source_partner)
    )
    by_partner = {r[0]: r[1] for r in (await db.execute(q_partner)).all()}

    q_typo = (
        select(Listing.typology, func.count(Listing.id))
        .where(where_clause).where(Listing.typology.isnot(None))
        .group_by(Listing.typology)
    )
    by_typo = {r[0]: r[1] for r in (await db.execute(q_typo)).all()}

    return ok(
        ListingStats(
            total_listings=total or 0,
            avg_price=float(avg_price) if avg_price else None,
            min_price=float(min_price) if min_price else None,
            max_price=float(max_price) if max_price else None,
            avg_area=float(avg_area) if avg_area else None,
            by_district=by_district,
            by_property_type=by_type,
            by_source_partner=by_partner,
            by_typology=by_typo,
        ),
        "Listing stats retrieved successfully",
        request,
    )
@router.get("/duplicates", response_model=ApiResponse[DuplicatesResponse], responses=ERROR_RESPONSES, operation_id="detect_duplicates")
async def detect_duplicates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Detect duplicate listings by source_url."""
    count_sub = (
        select(Listing.source_url)
        .where(Listing.source_url.isnot(None))
        .group_by(Listing.source_url)
        .having(func.count(Listing.id) > 1)
        .subquery()
    )
    total = (await db.execute(select(func.count()).select_from(count_sub))).scalar_one()

    query = (
        select(Listing.source_url, func.count(Listing.id).label("count"))
        .where(Listing.source_url.isnot(None))
        .group_by(Listing.source_url)
        .having(func.count(Listing.id) > 1)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    entries = [DuplicateEntry(source_url=r[0], count=r[1]) for r in (await db.execute(query)).all()]
    pages = math.ceil(total / page_size) if total > 0 else 0
    return ok(
        DuplicatesResponse(duplicates=entries, total=total),
        "Duplicates detected successfully",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )
# ══════════════════════════════════════════════════════════════════
#  DYNAMIC ROUTES — /{listing_id} must come LAST
# ═════════════════════════════════════════════════════════════════
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

    listing = await ListingService.get_listing_by_id(db,listing_id,)

    return ok(
        ListingDetailRead.model_validate(listing),
        "Listing retrieved successfully",
        request,
    )

@router.post(
        "", 
        response_model=ApiResponse[ListingDetailRead], 
        status_code=201, 
        responses={**ERROR_RESPONSES, 409: {"model": ApiResponse, "description": "Listing with this source_url already exists."}}, 
        operation_id="create_listing"
        )
async def create_listing(
    payload: ListingCreate, 
    request: Request, 
    db: AsyncSession = Depends(get_db)
    ):
    """Create a new listing manually."""
    listing = await ListingService.create_listing(db, payload)
    return ok(ListingDetailRead.model_validate(listing), "Listing created successfully", request)

@router.patch(
        "/{listing_id}", 
        response_model=ApiResponse[ListingDetailRead], 
        responses=ERROR_RESPONSES, 
        operation_id="update_listing"
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
        operation_id="delete_listing")
async def delete_listing(
    listing_id: UUID, 
    request: Request,
    db: AsyncSession = Depends(get_db)
    ):
    """Delete a listing (hard delete — cascades to media_assets and price_history)."""
    await ListingService.delete_listing(db, listing_id)
    return ok(None, "Listing deleted successfully", request)