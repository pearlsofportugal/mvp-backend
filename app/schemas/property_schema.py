"""Canonical PropertySchema — migrated from the original scraper.

This schema represents the normalized, strongly-typed internal representation
of a property listing. Used for validation and as an intermediate format
between raw scraped data and the database model.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Money(BaseModel):
    """Monetary value with currency."""

    amount: float | None = Field(None, ge=0, description="Monetary amount.")
    currency: str | None = Field(None, min_length=3, max_length=3, description="ISO 4217 currency code (e.g. 'EUR').")


class Address(BaseModel):
    """Structured property address."""

    country: str | None = Field(None, description="Country name or ISO code.")
    region: str | None = Field(None, description="District / region.")
    city: str | None = Field(None, description="County / city.")
    area: str | None = Field(None, description="Parish / neighbourhood.")
    postal_code: str | None = Field(None, description="Postal / ZIP code.")
    full_address: str | None = Field(None, description="Full formatted address string.")


class MediaAsset(BaseModel):
    """A single media asset (photo, floorplan, or video)."""

    url: str = Field(..., description="Absolute URL to the media asset.")
    alt_text: str | None = Field(None, description="Accessibility alt text.")
    type: Literal["photo", "floorplan", "video"] | None = Field(
        None,
        description="Asset type.",
    )
    position: int | None = Field(None, ge=0, description="Display order (0-indexed).")


class ListingFlags(BaseModel):
    """Boolean feature flags for a property listing."""

    has_garage: bool | None = None
    has_elevator: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_pool: bool | None = None
    is_new_construction: bool | None = None


class PropertySchema(BaseModel):
    """Canonical property schema — strongly typed, partner-agnostic.

    Acts as the normalised intermediate between raw scraped payloads and
    the database model. All partner-specific quirks should be resolved
    before populating this schema.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    internal_id: str | None = Field(None, description="Internal tracking ID (assigned post-ingestion).")
    partner_id: str | None = Field(None, description="Partner's own listing identifier.")
    source_partner: str = Field(..., description="Slug identifying the data source partner.")
    source_url: str | None = Field(None, description="Canonical URL of the listing on the partner site.")

    # ── Classification ────────────────────────────────────────────────────
    listing_type: Literal["sale", "rent"] | None = Field(None, description="Whether the listing is for sale or rent.")
    property_type: str | None = Field(None, description="Property type (e.g. 'apartment', 'house', 'office').")
    typology: str | None = Field(None, description="Portuguese typology code (e.g. 'T2', 'T3+1').")

    # ── Details ───────────────────────────────────────────────────────────
    title: str | None = Field(None, description="Listing headline.")
    bedrooms: int | None = Field(None, ge=0, description="Number of bedrooms.")
    bathrooms: int | None = Field(None, ge=0, description="Number of bathrooms.")
    floor: str | None = Field(None, description="Floor number or label (e.g. '3', 'R/C', 'último').")
    construction_year: int | None = Field(None, ge=1800, description="Year of construction.")
    energy_certificate: str | None = Field(None, description="Energy efficiency certificate rating.")

    # ── Pricing ───────────────────────────────────────────────────────────
    price: Money = Field(default_factory=Money, description="Asking price.")
    price_per_m2: Money | None = Field(None, description="Price per square metre.")

    # ── Area ──────────────────────────────────────────────────────────────
    area_useful_m2: float | None = Field(None, ge=0, description="Useful / habitable area in m².")
    area_gross_m2: float | None = Field(None, ge=0, description="Gross area in m².")
    area_land_m2: float | None = Field(None, ge=0, description="Land / plot area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    address: Address = Field(default_factory=Address)
    latitude: float | None = Field(None, ge=-90, le=90, description="WGS-84 latitude.")
    longitude: float | None = Field(None, ge=-180, le=180, description="WGS-84 longitude.")

    # ── Features ──────────────────────────────────────────────────────────
    features: ListingFlags = Field(default_factory=ListingFlags)

    # ── Media ─────────────────────────────────────────────────────────────
    media: list[MediaAsset] = Field(default_factory=list, description="Ordered list of media assets.")

    # ── Content ───────────────────────────────────────────────────────────
    descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="Localised descriptions keyed by ISO 639-1 language code (e.g. {'pt': '...', 'en': '...'}).",
    )
    description_quality_score: int | None = Field(None, ge=0, le=100, description="AI-assigned description quality score (0–100).")
    seo: dict | None = Field(None, description="SEO metadata (title, meta_description, keywords).")
    ai_content: dict | None = Field(None, description="Raw AI enrichment output.")

    # ── Contact / advertiser ──────────────────────────────────────────────
    advertiser: str | None = Field(None, description="Advertiser or agency name.")
    contacts: str | None = Field(None, description="Contact information (phone, email).")

    # ── Raw data ──────────────────────────────────────────────────────────
    raw_partner_payload: dict | None = Field(None, description="Original partner payload, preserved for debugging.")

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("source_partner")
    @classmethod
    def source_partner_must_be_slug(cls, v: str) -> str:
        """Ensure source_partner is a lowercase slug with no spaces."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("source_partner must be a lowercase alphanumeric slug (hyphens/underscores allowed).")
        return v.lower()