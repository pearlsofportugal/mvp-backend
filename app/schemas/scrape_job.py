"""Pydantic schemas for ScrapeJob API requests and responses."""
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobConfig(BaseModel):
    """Runtime configuration for a scrape job."""
    min_delay: float = Field(2.0, ge=0.5, description="Minimum delay between requests (seconds)")
    max_delay: float = Field(5.0, ge=1.0, description="Maximum delay between requests (seconds)")
    user_agent: Optional[str] = Field(
        None,
        description="Custom User-Agent string. Must include bot name and contact info.",
    )


class JobCreate(BaseModel):
    """Schema for creating a new scrape job."""
    site_key: str = Field(..., description="Site configuration key (e.g. 'pearls')")
    start_url: str = Field(..., description="URL to start scraping from")
    max_pages: int = Field(10, ge=1, le=100, description="Maximum number of pages to scrape")
    config: Optional[JobConfig] = None


class JobProgress(BaseModel):
    """Progress counters for a running job."""
    pages_visited: int = 0
    listings_found: int = 0
    listings_scraped: int = 0
    errors: int = 0


class JobRead(BaseModel):
    """Schema for scrape job responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_key: str
    base_url: Optional[str] = None
    start_url: str
    max_pages: int
    status: str
    progress: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    logs: Optional[Dict[str, Any]] = None
    urls: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class JobListRead(BaseModel):
    """Compact schema for job listing."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_key: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
