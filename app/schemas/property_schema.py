"""Canonical PropertySchema — migrated from the original scraper.

This schema represents the normalized, strongly-typed internal representation
of a property listing. Used for validation and as an intermediate format
between raw scraped data and the database model.
"""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, HttpUrl


class Money(BaseModel):
    amount: Optional[float] = None
    currency: Optional[str] = None


class Address(BaseModel):
    country: Optional[str] = None
    region: Optional[str] = None       # district
    city: Optional[str] = None         # county
    area: Optional[str] = None         # parish
    postal_code: Optional[str] = None
    full_address: Optional[str] = None


class MediaAsset(BaseModel):
    url: str
    alt_text: Optional[str] = None
    type: Optional[str] = None         # "photo", "floorplan", "video"


class ListingFlags(BaseModel):
    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None
    is_new_construction: Optional[bool] = None


class PropertySchema(BaseModel):
    """Canonical property schema — strongly typed, partner-agnostic."""
    internal_id: Optional[str] = None
    partner_id: Optional[str] = None
    source_partner: str
    source_url: Optional[str] = None
    title: Optional[str] = None
    listing_type: Optional[str] = None     # sale, rent
    property_type: Optional[str] = None
    typology: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    floor: Optional[str] = None
    price: Money = Money()
    price_per_m2: Optional[Money] = None
    area_useful_m2: Optional[float] = None
    area_gross_m2: Optional[float] = None
    area_land_m2: Optional[float] = None
    address: Address = Address()
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    media: List[MediaAsset] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    features: ListingFlags = ListingFlags()
    descriptions: Dict[str, str] = {}      # {"pt": "...", "en": "..."}
    seo: Optional[Dict] = None
    ai_content: Optional[Dict] = None
    raw_partner_payload: Optional[Dict] = None
    energy_certificate: Optional[str] = None
    construction_year: Optional[int] = None
    advertiser: Optional[str] = None
    contacts: Optional[str] = None
    description_quality_score: Optional[int] = None
