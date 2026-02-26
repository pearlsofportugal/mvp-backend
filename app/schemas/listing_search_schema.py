"""Pydantic schemas for GET /api/v1/listings/search"""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class ListingSearchItem(BaseModel):
    """Lightweight listing representation for the selector UI.

    Extends the base list fields with thumbnail_url (first media asset)
    and is_enriched (bool derived from enriched_description presence).
    """

    id: UUID
    source_partner: str

    title: Optional[str] = None
    property_type: Optional[str] = None
    typology: Optional[str] = None
    bedrooms: Optional[int] = None
    area_useful_m2: Optional[float] = None

    district: Optional[str] = None
    county: Optional[str] = None

    price_amount: Optional[float] = None
    price_currency: Optional[str] = None

    thumbnail_url: Optional[str] = Field(
        None,
        description="URL of the first media asset (position=0), if available",
    )
    is_enriched: bool = Field(
        False,
        description="True when enriched_description is not null/empty",
    )

    model_config = {"from_attributes": True}


class ListingSearchResponse(BaseModel):
    """Paginated search results for the listing selector."""

    items: list[ListingSearchItem]
    total: int
    page: int
    page_size: int
    pages: int