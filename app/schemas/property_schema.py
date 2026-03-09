"""Canonical PropertySchema — migrated from the original scraper.

This schema represents the normalized, strongly-typed internal representation
of a property listing. Used for validation and as an intermediate format
between raw scraped data and the database model.
"""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Money(BaseModel):
    """Monetary value with currency."""

    amount: Optional[float] = Field(None, ge=0, description="Monetary amount.")
    currency: Optional[str] = Field(None, min_length=3, max_length=3, description="ISO 4217 currency code (e.g. 'EUR').")


class Address(BaseModel):
    """Structured property address."""

    country: Optional[str] = Field(None, description="Country name or ISO code.")
    region: Optional[str] = Field(None, description="District / region.")
    city: Optional[str] = Field(None, description="County / city.")
    area: Optional[str] = Field(None, description="Parish / neighbourhood.")
    postal_code: Optional[str] = Field(None, description="Postal / ZIP code.")
    full_address: Optional[str] = Field(None, description="Full formatted address string.")


class MediaAsset(BaseModel):
    """A single media asset (photo, floorplan, or video)."""

    url: str = Field(..., description="Absolute URL to the media asset.")
    alt_text: Optional[str] = Field(None, description="Accessibility alt text.")
    type: Optional[Literal["photo", "floorplan", "video"]] = Field(
        None,
        description="Asset type.",
    )
    position: Optional[int] = Field(None, ge=0, description="Display order (0-indexed).")


class ListingFlags(BaseModel):
    """Boolean feature flags for a property listing."""

    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None
    is_new_construction: Optional[bool] = None


class PropertySchema(BaseModel):
    """Canonical property schema — strongly typed, partner-agnostic.

    Acts as the normalised intermediate between raw scraped payloads and
    the database model. All partner-specific quirks should be resolved
    before populating this schema.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    internal_id: Optional[str] = Field(None, description="Internal tracking ID (assigned post-ingestion).")
    partner_id: Optional[str] = Field(None, description="Partner's own listing identifier.")
    source_partner: str = Field(..., description="Slug identifying the data source partner.")
    source_url: Optional[str] = Field(None, description="Canonical URL of the listing on the partner site.")

    # ── Classification ────────────────────────────────────────────────────
    listing_type: Optional[Literal["sale", "rent"]] = Field(None, description="Whether the listing is for sale or rent.")
    property_type: Optional[str] = Field(None, description="Property type (e.g. 'apartment', 'house', 'office').")
    typology: Optional[str] = Field(None, description="Portuguese typology code (e.g. 'T2', 'T3+1').")

    # ── Details ───────────────────────────────────────────────────────────
    title: Optional[str] = Field(None, description="Listing headline.")
    bedrooms: Optional[int] = Field(None, ge=0, description="Number of bedrooms.")
    bathrooms: Optional[int] = Field(None, ge=0, description="Number of bathrooms.")
    floor: Optional[str] = Field(None, description="Floor number or label (e.g. '3', 'R/C', 'último').")
    construction_year: Optional[int] = Field(None, ge=1800, description="Year of construction.")
    energy_certificate: Optional[str] = Field(None, description="Energy efficiency certificate rating.")

    # ── Pricing ───────────────────────────────────────────────────────────
    price: Money = Field(default_factory=Money, description="Asking price.")
    price_per_m2: Optional[Money] = Field(None, description="Price per square metre.")

    # ── Area ──────────────────────────────────────────────────────────────
    area_useful_m2: Optional[float] = Field(None, ge=0, description="Useful / habitable area in m².")
    area_gross_m2: Optional[float] = Field(None, ge=0, description="Gross area in m².")
    area_land_m2: Optional[float] = Field(None, ge=0, description="Land / plot area in m².")

    # ── Location ──────────────────────────────────────────────────────────
    address: Address = Field(default_factory=Address)
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="WGS-84 latitude.")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="WGS-84 longitude.")

    # ── Features ──────────────────────────────────────────────────────────
    features: ListingFlags = Field(default_factory=ListingFlags)

    # ── Media ─────────────────────────────────────────────────────────────
    media: List[MediaAsset] = Field(default_factory=list, description="Ordered list of media assets.")

    # ── Content ───────────────────────────────────────────────────────────
    descriptions: Dict[str, str] = Field(
        default_factory=dict,
        description="Localised descriptions keyed by ISO 639-1 language code (e.g. {'pt': '...', 'en': '...'}).",
    )
    description_quality_score: Optional[int] = Field(None, ge=0, le=100, description="AI-assigned description quality score (0–100).")
    seo: Optional[Dict] = Field(None, description="SEO metadata (title, meta_description, keywords).")
    ai_content: Optional[Dict] = Field(None, description="Raw AI enrichment output.")

    # ── Contact / advertiser ──────────────────────────────────────────────
    advertiser: Optional[str] = Field(None, description="Advertiser or agency name.")
    contacts: Optional[str] = Field(None, description="Contact information (phone, email).")

    # ── Raw data ──────────────────────────────────────────────────────────
    raw_partner_payload: Optional[Dict] = Field(None, description="Original partner payload, preserved for debugging.")

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("source_partner")
    @classmethod
    def source_partner_must_be_slug(cls, v: str) -> str:
        """Ensure source_partner is a lowercase slug with no spaces."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("source_partner must be a lowercase alphanumeric slug (hyphens/underscores allowed).")
        return v.lower()