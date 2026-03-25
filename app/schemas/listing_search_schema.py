"""Pydantic schemas for GET /api/v1/listings/search."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListingSearchItem(BaseModel):
    """Lightweight listing representation for the selector UI.

    Extends the base list fields with ``thumbnail_url`` (first media asset at
    position=0) and ``is_enriched`` (True when any AI-enriched field is present).
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Identity ──────────────────────────────────────────────────────────
    id: UUID
    source_partner: str = Field(..., description="Slug identifying the data source partner.")

    # ── Display ───────────────────────────────────────────────────────────
    title: str | None = None
    property_type: str | None = None
    typology: str | None = Field(None, description="Portuguese typology code (e.g. 'T2').")
    bedrooms: int | None = Field(None, ge=0)
    area_useful_m2: float | None = Field(None, ge=0, description="Useful area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    district: str | None = None
    county: str | None = None

    # ── Financial ─────────────────────────────────────────────────────────
    listing_type: Literal["sale", "rent"] | None = None
    price_amount: Decimal | None = Field(None, ge=0)
    price_currency: str | None = Field(None, min_length=3, max_length=3, description="ISO 4217 currency code.")

    # ── Selector extras ───────────────────────────────────────────────────
    thumbnail_url: str | None = Field(
        None,
        description="URL of the first media asset (position=0), if available.",
    )
    is_enriched: bool = Field(
        False,
        description="True when any of enriched_title, enriched_description, or enriched_meta_description is present.",
    )


class ListingSearchResponse(BaseModel):
    """Search results for the listing selector.

    Pagination metadata (page, page_size, total, pages) is carried by
    ``ApiResponse.meta``.
    """

    items: list[ListingSearchItem] = Field(default_factory=list)