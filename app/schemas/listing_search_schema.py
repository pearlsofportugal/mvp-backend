"""Pydantic schemas for GET /api/v1/listings/search."""

from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListingSearchItem(BaseModel):
    """Lightweight listing representation for the selector UI.

    Extends the base list fields with ``thumbnail_url`` (first media asset,
    position=0) and ``is_enriched`` (derived from enriched_description presence).
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Identity ──────────────────────────────────────────────────────────
    id: UUID
    source_partner: str = Field(..., description="Slug identifying the data source partner.")

    # ── Display ───────────────────────────────────────────────────────────
    title: Optional[str] = None
    property_type: Optional[str] = None
    typology: Optional[str] = Field(None, description="Portuguese typology code (e.g. 'T2').")
    bedrooms: Optional[int] = Field(None, ge=0)
    area_useful_m2: Optional[float] = Field(None, ge=0, description="Useful area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    district: Optional[str] = None
    county: Optional[str] = None

    # ── Financial ─────────────────────────────────────────────────────────
    listing_type: Optional[Literal["sale", "rent"]] = None
    price_amount: Optional[Decimal] = Field(None, ge=0)
    price_currency: Optional[str] = Field(None, min_length=3, max_length=3, description="ISO 4217 currency code.")

    # ── Selector extras ───────────────────────────────────────────────────
    thumbnail_url: Optional[str] = Field(
        None,
        description="URL of the first media asset (position=0), if available.",
    )
    is_enriched: bool = Field(
        False,
        description="True when enriched_description is present and non-empty.",
    )


class ListingSearchResponse(BaseModel):
    """Search results for the listing selector.

    Pagination metadata (page, page_size, total, pages) is carried by
    ``ApiResponse.meta``.
    """

    items: List[ListingSearchItem] = Field(default_factory=list)