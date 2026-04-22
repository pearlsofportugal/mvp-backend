"""Shared listing filter helper — used by listings and export routers."""
from datetime import datetime
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql import Select

from app.config import settings
from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing


class ListingFilters(TypedDict, total=False):
    """Typed contract for listing filter parameters.

    Using TypedDict ensures typos in filter keys are caught by static
    analysis instead of silently producing no-op WHERE clauses.
    """

    district: str | None
    county: str | None
    parish: str | None
    property_type: str | None
    typology: str | None
    listing_type: str | None
    source_partner: str | None
    scrape_job_id: UUID | None
    price_min: Decimal | None
    price_max: Decimal | None
    area_min: float | None
    area_max: float | None
    bedrooms_min: int | None
    bedrooms_max: int | None
    has_garage: bool | None
    has_pool: bool | None
    has_elevator: bool | None
    created_after: datetime | None
    created_before: datetime | None
    search: str | None
    is_enriched: bool | None
    is_exported_to_imodigi: bool | None


def _use_postgres_fts() -> bool:
    return settings.database_url.startswith("postgresql")


def apply_listing_filters(query: Select, filters: ListingFilters) -> Select:
    """Apply dynamic filters to a listing SQLAlchemy query.

    All parameters are optional. Only non-None values produce WHERE clauses.
    Compatible with any base select() statement that targets Listing.
    """
    conds = []

    if filters.get("district"):
        conds.append(Listing.district.ilike(f"%{filters['district']}%"))
    if filters.get("county"):
        conds.append(Listing.county.ilike(f"%{filters['county']}%"))
    if filters.get("parish"):
        conds.append(Listing.parish.ilike(f"%{filters['parish']}%"))
    if filters.get("property_type"):
        conds.append(Listing.property_type.ilike(f"%{filters['property_type']}%"))
    if filters.get("typology"):
        conds.append(Listing.typology == filters["typology"])
    if filters.get("listing_type"):
        conds.append(Listing.listing_type == filters["listing_type"])
    if filters.get("source_partner"):
        conds.append(Listing.source_partner == filters["source_partner"])
    if filters.get("scrape_job_id"):
        conds.append(Listing.scrape_job_id == filters["scrape_job_id"])

    # Range filters
    if filters.get("price_min") is not None:
        conds.append(Listing.price_amount >= filters["price_min"])
    if filters.get("price_max") is not None:
        conds.append(Listing.price_amount <= filters["price_max"])
    if filters.get("area_min") is not None:
        conds.append(Listing.area_useful_m2 >= filters["area_min"])
    if filters.get("area_max") is not None:
        conds.append(Listing.area_useful_m2 <= filters["area_max"])
    if filters.get("bedrooms_min") is not None:
        conds.append(Listing.bedrooms >= filters["bedrooms_min"])
    if filters.get("bedrooms_max") is not None:
        conds.append(Listing.bedrooms <= filters["bedrooms_max"])

    # Boolean flags
    if filters.get("has_garage") is not None:
        conds.append(Listing.has_garage == filters["has_garage"])
    if filters.get("has_pool") is not None:
        conds.append(Listing.has_pool == filters["has_pool"])
    if filters.get("has_elevator") is not None:
        conds.append(Listing.has_elevator == filters["has_elevator"])

    # Date filters
    if filters.get("created_after"):
        conds.append(Listing.created_at >= filters["created_after"])
    if filters.get("created_before"):
        conds.append(Listing.created_at <= filters["created_before"])

    # Enrichment filter
    if filters.get("is_enriched") is True:
        conds.append(Listing.enriched_translations.isnot(None))
    elif filters.get("is_enriched") is False:
        conds.append(Listing.enriched_translations.is_(None))

    # Imodigi export filter
    if filters.get("is_exported_to_imodigi") is True:
        conds.append(
            exists(
                select(ImodigiExport.id).where(
                    ImodigiExport.listing_id == Listing.id,
                    ImodigiExport.status.in_(["published", "updated"]),
                ).correlate(Listing)
            )
        )
    elif filters.get("is_exported_to_imodigi") is False:
        conds.append(
            ~exists(
                select(ImodigiExport.id).where(
                    ImodigiExport.listing_id == Listing.id,
                    ImodigiExport.status.in_(["published", "updated"]),
                ).correlate(Listing)
            )
        )

    # Full-text search — uses TSVECTOR GIN index on PostgreSQL; ILIKE fallback for SQLite (tests)
    if filters.get("search"):
        if _use_postgres_fts():
            tsquery = func.plainto_tsquery("portuguese", filters["search"])
            conds.append(Listing.search_vector.op("@@")(tsquery))
        else:
            search_term = f"%{filters['search']}%"
            conds.append(or_(
                Listing.title.ilike(search_term),
                Listing.description.ilike(search_term),
            ))

    if conds:
        query = query.where(and_(*conds))

    return query
