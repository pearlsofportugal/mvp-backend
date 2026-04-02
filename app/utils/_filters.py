"""Shared listing filter helper — used by listings and export routers."""
from sqlalchemy import and_, or_

from app.models.listing_model import Listing


def apply_listing_filters(query, **kwargs):
    """Apply dynamic filters to a listing SQLAlchemy query.

    All parameters are optional. Only non-None values produce WHERE clauses.
    Compatible with any base select() statement that targets Listing.
    """
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
    if kwargs.get("listing_type"):
        filters.append(Listing.listing_type == kwargs["listing_type"])
    if kwargs.get("source_partner"):
        filters.append(Listing.source_partner == kwargs["source_partner"])
    if kwargs.get("scrape_job_id"):
        filters.append(Listing.scrape_job_id == kwargs["scrape_job_id"])

    # Range filters
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

    # Boolean flags
    if kwargs.get("has_garage") is not None:
        filters.append(Listing.has_garage == kwargs["has_garage"])
    if kwargs.get("has_pool") is not None:
        filters.append(Listing.has_pool == kwargs["has_pool"])
    if kwargs.get("has_elevator") is not None:
        filters.append(Listing.has_elevator == kwargs["has_elevator"])

    # Date filters
    if kwargs.get("created_after"):
        filters.append(Listing.created_at >= kwargs["created_after"])
    if kwargs.get("created_before"):
        filters.append(Listing.created_at <= kwargs["created_before"])

    # Full-text search
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
