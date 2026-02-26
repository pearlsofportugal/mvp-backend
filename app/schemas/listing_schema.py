"""Pydantic schemas for Listing API requests and responses."""
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class MediaAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    alt_text: Optional[str] = None
    type: Optional[str] = None
    position: Optional[int] = None


class MediaAssetCreate(BaseModel):
    url: str
    alt_text: Optional[str] = None
    type: Optional[str] = Field(None, pattern="^(photo|floorplan|video)$")
    position: Optional[int] = None


class PriceHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    price_amount: Decimal
    price_currency: str
    recorded_at: datetime


class ListingBase(BaseModel):
    """Shared fields for create and update."""
    title: Optional[str] = None
    listing_type: Optional[str] = Field(None, pattern="^(sale|rent)$")
    property_type: Optional[str] = None
    typology: Optional[str] = None
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    floor: Optional[str] = None

    price_amount: Optional[Decimal] = Field(None, ge=0)
    price_currency: Optional[str] = Field("EUR", max_length=3)
    price_per_m2: Optional[Decimal] = Field(None, ge=0)

    area_useful_m2: Optional[float] = Field(None, ge=0)
    area_gross_m2: Optional[float] = Field(None, ge=0)
    area_land_m2: Optional[float] = Field(None, ge=0)

    district: Optional[str] = None
    county: Optional[str] = None
    parish: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None

    energy_certificate: Optional[str] = None
    construction_year: Optional[int] = None

    advertiser: Optional[str] = None
    contacts: Optional[str] = None

    raw_description: Optional[str] = None
    description: Optional[str] = None
    enriched_description: Optional[str] = None
    description_quality_score: Optional[int] = Field(None, ge=0, le=100)
    meta_description: Optional[str] = None

    page_title: Optional[str] = None
    headers: Optional[Any] = None


class ListingCreate(ListingBase):
    """Schema for creating a new listing."""
    source_partner: str
    source_url: Optional[str] = None
    partner_id: Optional[str] = None
    media_assets: List[MediaAssetCreate] = []
    raw_payload: Optional[dict] = None


class ListingUpdate(BaseModel):
    """Schema for partial listing updates (all fields optional)."""
    title: Optional[str] = None
    listing_type: Optional[str] = None
    property_type: Optional[str] = None
    typology: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    floor: Optional[str] = None
    price_amount: Optional[Decimal] = None
    price_currency: Optional[str] = None
    price_per_m2: Optional[Decimal] = None
    area_useful_m2: Optional[float] = None
    area_gross_m2: Optional[float] = None
    area_land_m2: Optional[float] = None
    district: Optional[str] = None
    county: Optional[str] = None
    parish: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    has_garage: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_air_conditioning: Optional[bool] = None
    has_pool: Optional[bool] = None
    energy_certificate: Optional[str] = None
    construction_year: Optional[int] = None
    advertiser: Optional[str] = None
    contacts: Optional[str] = None
    description: Optional[str] = None
    enriched_description: Optional[str] = None
    description_quality_score: Optional[int] = None
    meta_description: Optional[str] = None


class ListingRead(ListingBase):
    """Schema for listing responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    partner_id: Optional[str] = None
    source_partner: str
    source_url: Optional[str] = None
    raw_payload: Optional[dict] = None
    scrape_job_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    media_assets: List[MediaAssetRead] = []
    price_history: List[PriceHistoryRead] = []


class ListingListRead(BaseModel):
    """Schema for listing list responses with pagination."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: Optional[str] = None
    source_partner: str
    property_type: Optional[str] = None
    typology: Optional[str] = None
    price_amount: Optional[Decimal] = None
    price_currency: Optional[str] = None
    district: Optional[str] = None
    county: Optional[str] = None
    area_useful_m2: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ListingStats(BaseModel):
    """Aggregated listing statistics."""
    total_listings: int = 0
    avg_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    avg_area: Optional[float] = None
    by_district: dict[str, int] = {}
    by_property_type: dict[str, int] = {}
    by_source_partner: dict[str, int] = {}
    by_typology: dict[str, int] = {}


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: List[ListingListRead]
    total: int
    page: int
    page_size: int
    pages: int
