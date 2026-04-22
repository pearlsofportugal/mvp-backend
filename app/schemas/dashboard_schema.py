"""Pydantic schemas for GET /api/v1/dashboard/partners."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PartnerStats(BaseModel):
    """Statistics row for a single source partner."""

    source_partner: str = Field(..., description="Partner slug (e.g. 'pearls', 'habinedita').")

    # Volume
    total_listings: int = Field(0, ge=0, description="Total listings currently in the DB.")
    listings_updated_last_7_days: int = Field(0, ge=0, description="Listings updated in the past 7 days.")

    # Pricing
    avg_price: float | None = Field(None, ge=0, description="Average listing price.")
    min_price: float | None = Field(None, ge=0, description="Minimum listing price.")
    max_price: float | None = Field(None, ge=0, description="Maximum listing price.")

    # Enrichment / export
    enriched_count: int = Field(0, ge=0, description="Listings with AI enrichment.")
    exported_to_imodigi_count: int = Field(0, ge=0, description="Listings successfully exported to Imodigi.")

    # Timestamps
    last_listing_updated_at: datetime | None = Field(
        None, description="Most recent updated_at across all listings of this partner."
    )

    # Last scrape job
    last_job_id: str | None = Field(None, description="UUID of the most recent scrape job.")
    last_job_status: str | None = Field(None, description="Status of the most recent scrape job.")
    last_job_at: datetime | None = Field(None, description="created_at of the most recent scrape job.")
    last_job_scraped_count: int | None = Field(None, ge=0, description="Listings scraped in the last job.")


class PartnerStatsResponse(BaseModel):
    """Response body for GET /api/v1/dashboard/partners."""

    partners: list[PartnerStats] = Field(default_factory=list)
    total_partners: int = Field(0, ge=0)
