"""Pydantic schemas for Listing API requests and responses."""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional
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
    alt_text: Optional[str] = None
    type: Optional[Literal["photo", "floorplan", "video"]] = None
    position: Optional[int] = Field(None, ge=0)


class MediaAssetCreate(BaseModel):
    """Media asset payload for listing creation."""

    url: str = Field(..., description="Absolute URL to the media asset.")
    alt_text: Optional[str] = Field(None, description="Accessibility alt text.")
    type: Optional[Literal["photo", "floorplan", "video"]] = Field(
        None,
        description="Asset type: photo, floorplan, or video.",
    )
    position: Optional[int] = Field(None, ge=0, description="Display order (0-indexed).")


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
    listing_type: Optional[Literal["sale", "rent"]] = Field(None, description="Listing transaction type.")
    property_type: Optional[str] = Field(None, description="Property type (e.g. 'apartment', 'house').")
    typology: Optional[str] = Field(None, description="Portuguese typology code (e.g. 'T2', 'T3+1').")

    # ── Details ───────────────────────────────────────────────────────────
    title: Optional[str] = Field(None, description="Listing headline.")
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    floor: Optional[str] = Field(None, description="Floor label (e.g. '3', 'R/C', 'último').")
    construction_year: Optional[int] = Field(None, ge=1800, description="Year of construction.")
    energy_certificate: Optional[str] = Field(None, description="Energy certificate rating.")

    # ── Pricing ───────────────────────────────────────────────────────────
    price_amount: Optional[Decimal] = Field(None, ge=0)
    price_currency: Optional[str] = Field("EUR", min_length=3, max_length=3, description="ISO 4217 currency code.")
    price_per_m2: Optional[Decimal] = Field(None, ge=0)

    # ── Area ──────────────────────────────────────────────────────────────
    area_useful_m2: Optional[float] = Field(None, ge=0, description="Useful / habitable area in m².")
    area_gross_m2: Optional[float] = Field(None, ge=0, description="Gross area in m².")
    area_land_m2: Optional[float] = Field(None, ge=0, description="Land / plot area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    district: Optional[str] = None
    county: Optional[str] = None
    parish: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="WGS-84 latitude.")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="WGS-84 longitude.")

    # ── Features ──────────────────────────────────────────────────────────
    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None

    # ── Contact / advertiser ──────────────────────────────────────────────
    advertiser: Optional[str] = None
    contacts: Optional[str] = None

    # ── Content ───────────────────────────────────────────────────────────
    raw_description: Optional[str] = Field(None, description="Raw description as scraped (unprocessed).")
    description: Optional[str] = Field(None, description="Cleaned / normalised description.")
    enriched_description: Optional[str] = Field(None, description="AI-enriched description.")
    description_quality_score: Optional[int] = Field(None, ge=0, le=100, description="AI quality score (0–100).")
    meta_description: Optional[str] = Field(None, description="SEO meta description.")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class ListingCreate(ListingBase):
    """Schema for creating a new listing (scraper → API)."""

    source_partner: str = Field(..., description="Slug identifying the data source partner.")
    source_url: Optional[str] = Field(None, description="Canonical URL of the listing on the partner site.")
    partner_id: Optional[str] = Field(None, description="Partner's own listing identifier.")
    page_title: Optional[str] = Field(None, description="Raw <title> of the scraped page.")
    media_assets: List[MediaAssetCreate] = Field(default_factory=list)
    raw_payload: Optional[Dict] = Field(None, description="Full raw partner payload, preserved for debugging.")


# ---------------------------------------------------------------------------
# Update (PATCH — all fields optional)
# ---------------------------------------------------------------------------

class ListingUpdate(BaseModel):
    """Schema for partial listing updates (PATCH).

    Intentionally omits scraper-internal fields (``page_title``,
    ``raw_description``) to prevent accidental overwrites via the API.
    Only supplied fields are applied.
    """

    listing_type: Optional[Literal["sale", "rent"]] = None
    property_type: Optional[str] = None
    typology: Optional[str] = None
    title: Optional[str] = None
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    floor: Optional[str] = None
    construction_year: Optional[int] = Field(None, ge=1800)
    energy_certificate: Optional[str] = None
    price_amount: Optional[Decimal] = Field(None, ge=0)
    price_currency: Optional[str] = Field(None, min_length=3, max_length=3)
    price_per_m2: Optional[Decimal] = Field(None, ge=0)
    area_useful_m2: Optional[float] = Field(None, ge=0)
    area_gross_m2: Optional[float] = Field(None, ge=0)
    area_land_m2: Optional[float] = Field(None, ge=0)
    district: Optional[str] = None
    county: Optional[str] = None
    parish: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None
    advertiser: Optional[str] = None
    contacts: Optional[str] = None
    description: Optional[str] = None
    enriched_description: Optional[str] = None
    description_quality_score: Optional[int] = Field(None, ge=0, le=100)
    meta_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Read (detail)
# ---------------------------------------------------------------------------

class ListingRead(ListingBase):
    """Full listing detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    partner_id: Optional[str] = None
    source_partner: str
    source_url: Optional[str] = None
    scrape_job_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    media_assets: List[MediaAssetRead] = Field(default_factory=list)
    price_history: List[PriceHistoryRead] = Field(default_factory=list)


class ListingDetailRead(BaseModel):
    """Public listing detail response without internal raw scraped text."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    partner_id: Optional[str] = None
    source_partner: str
    source_url: Optional[str] = None
    scrape_job_id: Optional[UUID] = None

    listing_type: Optional[Literal["sale", "rent"]] = None
    property_type: Optional[str] = None
    typology: Optional[str] = None
    title: Optional[str] = None
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    floor: Optional[str] = None
    construction_year: Optional[int] = Field(None, ge=1800)
    energy_certificate: Optional[str] = None

    price_amount: Optional[Decimal] = Field(None, ge=0)
    price_currency: Optional[str] = Field("EUR", min_length=3, max_length=3)
    price_per_m2: Optional[Decimal] = Field(None, ge=0)

    area_useful_m2: Optional[float] = Field(None, ge=0)
    area_gross_m2: Optional[float] = Field(None, ge=0)
    area_land_m2: Optional[float] = Field(None, ge=0)

    district: Optional[str] = None
    county: Optional[str] = None
    parish: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None

    advertiser: Optional[str] = None
    contacts: Optional[str] = None
    description: Optional[str] = None
    enriched_description: Optional[str] = None
    description_quality_score: Optional[int] = Field(None, ge=0, le=100)
    meta_description: Optional[str] = None

    created_at: datetime
    updated_at: datetime
    media_assets: List[MediaAssetRead] = Field(default_factory=list)
    price_history: List[PriceHistoryRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Read (list / paginated)
# ---------------------------------------------------------------------------

class ListingListRead(BaseModel):
    """Compact listing schema for paginated list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: Optional[str] = None
    source_partner: str
    listing_type: Optional[Literal["sale", "rent"]] = None
    property_type: Optional[str] = None
    typology: Optional[str] = None
    price_amount: Optional[Decimal] = None
    price_currency: Optional[str] = None
    price_per_m2: Optional[Decimal] = None
    district: Optional[str] = None
    county: Optional[str] = None
    area_useful_m2: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Stats & pagination
# ---------------------------------------------------------------------------

class ListingStats(BaseModel):
    """Aggregated listing statistics."""

    total_listings: int = 0
    avg_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    avg_area: Optional[float] = None
    by_district: Dict[str, int] = Field(default_factory=dict)
    by_property_type: Dict[str, int] = Field(default_factory=dict)
    by_source_partner: Dict[str, int] = Field(default_factory=dict)
    by_typology: Dict[str, int] = Field(default_factory=dict)


class PaginatedResponse(BaseModel):
    """Paginated listing items wrapper.

    Pagination metadata (page, page_size, total, pages) is carried by
    ``ApiResponse.meta`` so the frontend reads it from a single, consistent
    location regardless of which endpoint it calls.
    """

    items: List[ListingListRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

class DuplicateEntry(BaseModel):
    """A group of listings sharing the same source_url."""

    source_url: str
    count: int = Field(..., ge=2, description="Number of listings with this URL.")


class DuplicatesResponse(BaseModel):
    """Response body for GET /duplicates."""

    duplicates: List[DuplicateEntry] = Field(default_factory=list)