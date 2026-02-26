"""Pydantic schemas for SiteConfig API requests and responses."""
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SiteConfigBase(BaseModel):
    """Shared fields for site configuration."""
    name: str = Field(..., description="Human-readable site name")
    base_url: str = Field(..., description="Base URL of the site")
    selectors: Dict[str, Any] = Field(
        default_factory=dict,
        description="CSS selectors for parsing: listing_link, title, price, description, etc.",
    )
    extraction_mode: str = Field(
        "direct",
        pattern="^(section|direct)$",
        description="Extraction mode: 'section' (name/value pairs) or 'direct' (CSS selectors)",
    )
    link_pattern: Optional[str] = Field(None, description="Regex pattern to filter listing URLs")
    image_filter: Optional[str] = Field(None, description="Pattern to filter image URLs")
    is_active: bool = True


class SiteConfigCreate(SiteConfigBase):
    """Schema for creating a new site configuration."""
    key: str = Field(..., min_length=2, max_length=50, description="Unique site identifier")


class SiteConfigUpdate(BaseModel):
    """Schema for partial site configuration updates."""
    name: Optional[str] = None
    base_url: Optional[str] = None
    selectors: Optional[Dict[str, Any]] = None
    extraction_mode: Optional[str] = None
    link_pattern: Optional[str] = None
    image_filter: Optional[str] = None
    is_active: Optional[bool] = None


class SiteConfigRead(SiteConfigBase):
    """Schema for site configuration responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    created_at: datetime
    updated_at: datetime
