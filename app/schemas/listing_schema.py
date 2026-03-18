"""Pydantic schemas for Listing API requests and responses."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

class MediaAssetRead(BaseModel):
    """Media asset as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    alt_text: str | None = None
    type: Literal["photo", "floorplan", "video"] | None = None
    position: int | None = Field(None, ge=0)


class MediaAssetCreate(BaseModel):
    """Media asset payload for listing creation."""

    url: str = Field(..., description="Absolute URL to the media asset.")
    alt_text: str | None = Field(None, description="Accessibility alt text.")
    type: Literal["photo", "floorplan", "video"] | None = Field(
        None,
        description="Asset type: photo, floorplan, or video.",
    )
    position: int | None = Field(None, ge=0, description="Display order (0-indexed).")


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

class PriceHistoryRead(BaseModel):
    """A single price history entry."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    price_amount: Decimal = Field(..., ge=0)
    price_currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code.")
    recorded_at: datetime


# ---------------------------------------------------------------------------
# Listing base — shared fields for create and update
# ---------------------------------------------------------------------------

class ListingBase(BaseModel):
    """Fields shared across create, update, and read schemas.

    All fields are optional to support both partial updates and sparse
    scraped data. Field-level constraints are enforced where applicable.
    """

    # ── Classification ────────────────────────────────────────────────────
    listing_type: Literal["sale", "rent"] | None = Field(None, description="Listing transaction type.")
    property_type: str | None = Field(None, description="Property type (e.g. 'apartment', 'house').")
    typology: str | None = Field(None, description="Portuguese typology code (e.g. 'T2', 'T3+1').")

    # ── Details ───────────────────────────────────────────────────────────
    title: str | None = Field(None, description="Listing headline.")
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    floor: str | None = Field(None, description="Floor label (e.g. '3', 'R/C', 'último').")
    construction_year: int | None = Field(None, ge=1800, description="Year of construction.")
    energy_certificate: str | None = Field(None, description="Energy certificate rating.")

    # ── Pricing ───────────────────────────────────────────────────────────
    price_amount: Decimal | None = Field(None, ge=0)
    price_currency: str | None = Field("EUR", min_length=3, max_length=3, description="ISO 4217 currency code.")
    price_per_m2: Decimal | None = Field(None, ge=0)

    # ── Area ──────────────────────────────────────────────────────────────
    area_useful_m2: float | None = Field(None, ge=0, description="Useful / habitable area in m².")
    area_gross_m2: float | None = Field(None, ge=0, description="Gross area in m².")
    area_land_m2: float | None = Field(None, ge=0, description="Land / plot area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    district: str | None = None
    county: str | None = None
    parish: str | None = None
    full_address: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90, description="WGS-84 latitude.")
    longitude: float | None = Field(None, ge=-180, le=180, description="WGS-84 longitude.")

    # ── Features ──────────────────────────────────────────────────────────
    has_garage: bool | None = None
    has_elevator: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_pool: bool | None = None

    # ── Contact / advertiser ──────────────────────────────────────────────
    advertiser: str | None = None
    contacts: str | None = None

    # ── Content ───────────────────────────────────────────────────────────
    raw_description: str | None = Field(None, description="Raw description as scraped (unprocessed).")
    description: str | None = Field(None, description="Cleaned / normalised description.")
    enriched_description: str | None = Field(None, description="AI-enriched description.")
    description_quality_score: int | None = Field(None, ge=0, le=100, description="AI quality score (0–100).")
    meta_description: str | None = Field(None, description="SEO meta description.")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class ListingCreate(ListingBase):
    """Schema for creating a new listing (scraper → API)."""

    source_partner: str = Field(..., description="Slug identifying the data source partner.")
    source_url: str | None = Field(None, description="Canonical URL of the listing on the partner site.")
    partner_id: str | None = Field(None, description="Partner's own listing identifier.")
    page_title: str | None = Field(None, description="Raw <title> of the scraped page.")
    media_assets: list[MediaAssetCreate] = Field(default_factory=list)
    raw_payload: dict | None = Field(None, description="Full raw partner payload, preserved for debugging.")


# ---------------------------------------------------------------------------
# Update (PATCH — all fields optional)
# ---------------------------------------------------------------------------

class ListingUpdate(BaseModel):
    """Schema for partial listing updates (PATCH).

    Intentionally omits scraper-internal fields (``page_title``,
    ``raw_description``) to prevent accidental overwrites via the API.
    Only supplied fields are applied.
    """

    listing_type: Literal["sale", "rent"] | None = None
    property_type: str | None = None
    typology: str | None = None
    title: str | None = None
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    floor: str | None = None
    construction_year: int | None = Field(None, ge=1800)
    energy_certificate: str | None = None
    price_amount: Decimal | None = Field(None, ge=0)
    price_currency: str | None = Field(None, min_length=3, max_length=3)
    price_per_m2: Decimal | None = Field(None, ge=0)
    area_useful_m2: float | None = Field(None, ge=0)
    area_gross_m2: float | None = Field(None, ge=0)
    area_land_m2: float | None = Field(None, ge=0)
    district: str | None = None
    county: str | None = None
    parish: str | None = None
    full_address: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    has_garage: bool | None = None
    has_elevator: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_pool: bool | None = None
    advertiser: str | None = None
    contacts: str | None = None
    description: str | None = None
    enriched_description: str | None = None
    description_quality_score: int | None = Field(None, ge=0, le=100)
    meta_description: str | None = None


# ---------------------------------------------------------------------------
# Read (detail)
# ---------------------------------------------------------------------------

class ListingRead(ListingBase):
    """Full listing detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    partner_id: str | None = None
    source_partner: str
    source_url: str | None = None
    scrape_job_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    media_assets: list[MediaAssetRead] = Field(default_factory=list)
    price_history: list[PriceHistoryRead] = Field(default_factory=list)


class ListingDetailRead(BaseModel):
    """Public listing detail response without internal raw scraped text."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    partner_id: str | None = None
    source_partner: str
    source_url: str | None = None
    scrape_job_id: UUID | None = None

    listing_type: Literal["sale", "rent"] | None = None
    property_type: str | None = None
    typology: str | None = None
    title: str | None = None
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    floor: str | None = None
    construction_year: int | None = Field(None, ge=1800)
    energy_certificate: str | None = None

    price_amount: Decimal | None = Field(None, ge=0)
    price_currency: str | None = Field("EUR", min_length=3, max_length=3)
    price_per_m2: Decimal | None = Field(None, ge=0)

    area_useful_m2: float | None = Field(None, ge=0)
    area_gross_m2: float | None = Field(None, ge=0)
    area_land_m2: float | None = Field(None, ge=0)

    district: str | None = None
    county: str | None = None
    parish: str | None = None
    full_address: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    has_garage: bool | None = None
    has_elevator: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_pool: bool | None = None

    advertiser: str | None = None
    contacts: str | None = None
    description: str | None = None
    enriched_description: str | None = None
    description_quality_score: int | None = Field(None, ge=0, le=100)
    meta_description: str | None = None

    created_at: datetime
    updated_at: datetime
    media_assets: list[MediaAssetRead] = Field(default_factory=list)
    price_history: list[PriceHistoryRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Read (list / paginated)
# ---------------------------------------------------------------------------

class ListingListRead(BaseModel):
    """Compact listing schema for paginated list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None = None
    source_partner: str
    listing_type: Literal["sale", "rent"] | None = None
    property_type: str | None = None
    typology: str | None = None
    price_amount: Decimal | None = None
    price_currency: str | None = None
    price_per_m2: Decimal | None = None
    district: str | None = None
    county: str | None = None
    area_useful_m2: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    source_url: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Stats & pagination
# ---------------------------------------------------------------------------

class ListingStats(BaseModel):
    """Aggregated listing statistics."""

    total_listings: int = 0
    avg_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    avg_area: float | None = None
    by_district: dict[str, int] = Field(default_factory=dict)
    by_property_type: dict[str, int] = Field(default_factory=dict)
    by_source_partner: dict[str, int] = Field(default_factory=dict)
    by_typology: dict[str, int] = Field(default_factory=dict)


class PaginatedResponse(BaseModel):
    """Paginated listing items wrapper.

    Pagination metadata (page, page_size, total, pages) is carried by
    ``ApiResponse.meta`` so the frontend reads it from a single, consistent
    location regardless of which endpoint it calls.
    """

    items: list[ListingListRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

class DuplicateEntry(BaseModel):
    """A group of listings sharing the same source_url."""

    source_url: str
    count: int = Field(..., ge=2, description="Number of listings with this URL.")


class DuplicatesResponse(BaseModel):
    """Response body for GET /duplicates."""

    duplicates: list[DuplicateEntry] = Field(default_factory=list)