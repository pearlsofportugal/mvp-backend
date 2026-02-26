"""Listings API router — full CRUD with filtering, pagination, sorting, stats.
/api/v1/listings"""
import math
from decimal import Decimal
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, or_, select, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.core.exceptions import NotFoundError, DuplicateError
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.schemas.base_schema import ApiResponse
from app.schemas.listing_schema import (
    ListingCreate,
    ListingListRead,
    ListingRead,
    ListingStats,
    ListingUpdate,
    MediaAssetCreate,
    PaginatedResponse,
)
from app.api.responses import ok
from app.schemas.listing_search_schema import ListingSearchItem, ListingSearchResponse

router = APIRouter()


def _apply_filters(query, **kwargs):
    """Apply dynamic filters to a listing query."""
    filters = []

    if kwargs.get("district"):
        filters.append(Listing.district.ilike(f"%{kwargs['district']}%"))
    if kwargs.get("county"):
        filters.append(Listing.county.ilike(f"%{kwargs['county']}%"))
    if kwargs.get("parish"):
        filters.append(Listing.parish.ilike(f"%{kwargs['parish']}%"))
    if kwargs.get("property_type"):
        filters.append(Listing.property_type.ilike(f"%{kwargs['property_type']}%"))
    if kwargs.get("typology"):
        filters.append(Listing.typology == kwargs["typology"])
    if kwargs.get("source_partner"):
        filters.append(Listing.source_partner == kwargs["source_partner"])
    if kwargs.get("scrape_job_id"):
        filters.append(Listing.scrape_job_id == kwargs["scrape_job_id"])

    if kwargs.get("price_min") is not None:
        filters.append(Listing.price_amount >= kwargs["price_min"])
    if kwargs.get("price_max") is not None:
        filters.append(Listing.price_amount <= kwargs["price_max"])
    if kwargs.get("area_min") is not None:
        filters.append(Listing.area_useful_m2 >= kwargs["area_min"])
    if kwargs.get("area_max") is not None:
        filters.append(Listing.area_useful_m2 <= kwargs["area_max"])
    if kwargs.get("bedrooms_min") is not None:
        filters.append(Listing.bedrooms >= kwargs["bedrooms_min"])
    if kwargs.get("bedrooms_max") is not None:
        filters.append(Listing.bedrooms <= kwargs["bedrooms_max"])

    if kwargs.get("has_garage") is not None:
        filters.append(Listing.has_garage == kwargs["has_garage"])
    if kwargs.get("has_pool") is not None:
        filters.append(Listing.has_pool == kwargs["has_pool"])
    if kwargs.get("has_elevator") is not None:
        filters.append(Listing.has_elevator == kwargs["has_elevator"])

    if kwargs.get("created_after"):
        filters.append(Listing.created_at >= kwargs["created_after"])
    if kwargs.get("created_before"):
        filters.append(Listing.created_at <= kwargs["created_before"])

    if kwargs.get("search"):
        search_term = f"%{kwargs['search']}%"
        filters.append(
            or_(
                Listing.title.ilike(search_term),
                Listing.description.ilike(search_term),
                Listing.enriched_description.ilike(search_term),
            )
        )

    if filters:
        query = query.where(and_(*filters))

    return query


SORT_FIELDS = {
    "price": Listing.price_amount,
    "area": Listing.area_useful_m2,
    "bedrooms": Listing.bedrooms,
    "created_at": Listing.created_at,
    "updated_at": Listing.updated_at,
    "district": Listing.district,
    "title": Listing.title,
}




@router.get("", response_model=ApiResponse[PaginatedResponse])
async def list_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    district: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    parish: Optional[str] = Query(None),
    property_type: Optional[str] = Query(None),
    typology: Optional[str] = Query(None),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
    price_min: Optional[Decimal] = Query(None),
    price_max: Optional[Decimal] = Query(None),
    area_min: Optional[float] = Query(None),
    area_max: Optional[float] = Query(None),
    bedrooms_min: Optional[int] = Query(None),
    bedrooms_max: Optional[int] = Query(None),
    has_garage: Optional[bool] = Query(None),
    has_pool: Optional[bool] = Query(None),
    has_elevator: Optional[bool] = Query(None),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("created_at", enum=list(SORT_FIELDS.keys())),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List listings with filtering, sorting, and pagination."""
    filter_kwargs = {
        "district": district, "county": county, "parish": parish,
        "property_type": property_type, "typology": typology,
        "source_partner": source_partner, "scrape_job_id": scrape_job_id,
        "price_min": price_min, "price_max": price_max,
        "area_min": area_min, "area_max": area_max,
        "bedrooms_min": bedrooms_min, "bedrooms_max": bedrooms_max,
        "has_garage": has_garage, "has_pool": has_pool, "has_elevator": has_elevator,
        "created_after": created_after, "created_before": created_before,
        "search": search,
    }

    count_query = _apply_filters(select(func.count(Listing.id)), **filter_kwargs)
    total = (await db.execute(count_query)).scalar_one()

    query = _apply_filters(select(Listing), **filter_kwargs)
    sort_column = SORT_FIELDS.get(sort_by, Listing.created_at)
    query = query.order_by(desc(sort_column) if sort_order == "desc" else asc(sort_column))
    query = query.offset((page - 1) * page_size).limit(page_size)

    listings = (await db.execute(query)).scalars().all()

    return ok(
        PaginatedResponse(
            items=[ListingListRead.model_validate(l) for l in listings],
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total > 0 else 0,
        ),
        "Listings listed successfully",
        request,
    )
@router.get("/search", response_model=ApiResponse[ListingSearchResponse])
async def search_listings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = Query(None, min_length=2),
    source_partner: Optional[str] = Query(None),
    is_enriched: Optional[bool] = Query(None),
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
        thumbnail_url: Optional[str] = None
        if listing.media_assets:
            first = min(listing.media_assets, key=lambda m: m.position or 999)
            thumbnail_url = first.url

        items.append(
            ListingSearchItem(
                id=listing.id,
                source_partner=listing.source_partner,
                title=listing.title,
                property_type=listing.property_type,
                typology=listing.typology,
                bedrooms=listing.bedrooms,
                area_useful_m2=listing.area_useful_m2,
                district=listing.district,
                county=listing.county,
                price_amount=listing.price_amount,
                price_currency=listing.price_currency,
                thumbnail_url=thumbnail_url,
                is_enriched=bool(listing.enriched_description),
            )
        )

    return ok(
        ListingSearchResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total > 0 else 0,
        ),
        "Listings found",
        request,
    )
@router.get("/stats", response_model=ApiResponse[ListingStats])
async def listing_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
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
@router.get("/duplicates", response_model=ApiResponse[dict])
async def detect_duplicates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Detect duplicate listings by source_url."""
    query = (
        select(Listing.source_url, func.count(Listing.id).label("count"))
        .where(Listing.source_url.isnot(None))
        .group_by(Listing.source_url)
        .having(func.count(Listing.id) > 1)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    duplicates = [{"source_url": r[0], "count": r[1]} for r in (await db.execute(query)).all()]
    return ok({"duplicates": duplicates}, "Duplicates detected successfully", request)

@router.get("/{listing_id}", response_model=ApiResponse[ListingRead])
async def get_listing(listing_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Get a single listing by ID."""
    listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")
    return ok(ListingRead.model_validate(listing), "Listing retrieved successfully", request)


@router.post("", response_model=ApiResponse[ListingRead], status_code=201)
async def create_listing(payload: ListingCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new listing manually."""
    if payload.source_url:
        if (await db.execute(select(Listing).where(Listing.source_url == payload.source_url))).scalar_one_or_none():
            raise DuplicateError(f"Listing with source_url '{payload.source_url}' already exists")

    data = payload.model_dump(exclude={"media_assets"})
    listing = Listing(**data)
    db.add(listing)
    await db.flush()

    for asset_data in payload.media_assets:
        db.add(MediaAsset(listing_id=listing.id, **asset_data.model_dump()))

    await db.flush()
    listing = (await db.execute(select(Listing).where(Listing.id == listing.id))).scalar_one()
    return ok(ListingRead.model_validate(listing), "Listing created successfully", request)


@router.patch("/{listing_id}", response_model=ApiResponse[ListingRead])
async def update_listing(
    listing_id: UUID,
    payload: ListingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a listing."""
    listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "price_amount" in update_data and update_data["price_amount"] is not None:
        if listing.price_amount is not None and listing.price_amount != update_data["price_amount"]:
            db.add(PriceHistory(
                listing_id=listing.id,
                price_amount=listing.price_amount,
                price_currency=listing.price_currency or "EUR",
            ))

    for field, value in update_data.items():
        setattr(listing, field, value)

    await db.flush()
    listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one()
    return ok(ListingRead.model_validate(listing), "Listing updated successfully", request)



@router.delete("/{listing_id}", response_model=ApiResponse[None], status_code=200)
async def delete_listing(listing_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a listing (hard delete — cascades to media_assets and price_history)."""
    listing = (await db.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")
    await db.delete(listing)
    return ok(None, "Listing deleted successfully", request)