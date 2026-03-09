"""Pydantic schemas for SiteConfig API requests and responses."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ExtractionMode = Literal["section", "direct"]


# ---------------------------------------------------------------------------
# Base (shared fields)
# ---------------------------------------------------------------------------

class SiteConfigBase(BaseModel):
    """Fields shared across create, update, and read schemas."""

    name: str = Field(..., min_length=1, description="Human-readable site name.")
    base_url: str = Field(..., description="Base URL of the target site (e.g. 'https://example.com').")
    selectors: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "CSS selectors used for parsing. Expected keys: "
            "listing_link, title, price, description, images, next_page."
        ),
    )
    extraction_mode: ExtractionMode = Field(
        "direct",
        description=(
            "Extraction strategy: 'direct' applies CSS selectors to named fields; "
            "'section' parses name/value pairs from structured sections."
        ),
    )
    link_pattern: Optional[str] = Field(None, description="Regex pattern to filter listing URLs on listing pages.")
    image_filter: Optional[str] = Field(None, description="Regex pattern to filter image URLs (e.g. exclude thumbnails).")
    is_active: bool = Field(True, description="Whether this site config is enabled for scraping.")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class SiteConfigCreate(SiteConfigBase):
    """Schema for creating a new site configuration."""

    key: str = Field(
        ...,
        min_length=2,
        max_length=50,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="Unique site identifier slug (lowercase alphanumeric, hyphens and underscores allowed).",
    )


# ---------------------------------------------------------------------------
# Update (PATCH — all fields optional)
# ---------------------------------------------------------------------------

class SiteConfigUpdate(BaseModel):
    """Schema for partial site configuration updates (PATCH).

    All fields are optional; only supplied fields are applied.
    """

    name: Optional[str] = Field(None, min_length=1)
    base_url: Optional[str] = None
    selectors: Optional[Dict[str, Any]] = None
    extraction_mode: Optional[ExtractionMode] = None
    link_pattern: Optional[str] = None
    image_filter: Optional[str] = None
    is_active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

class SiteConfigRead(SiteConfigBase):
    """Full site configuration response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    created_at: datetime
    updated_at: datetime