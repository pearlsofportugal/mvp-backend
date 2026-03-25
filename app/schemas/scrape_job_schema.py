"""Pydantic schemas for ScrapeJob API requests and responses."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Exhaustive literal — emitted as a union type in generated TypeScript clients.
JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------

class JobConfig(BaseModel):
    """Runtime configuration for a scrape job."""

    min_delay: float = Field(2.0, ge=0.5, description="Minimum delay between requests (seconds).")
    max_delay: float = Field(5.0, ge=1.0, description="Maximum delay between requests (seconds).")
    user_agent: str | None = Field(
        None,
        description="Custom User-Agent string. Should include bot name and a contact URL or email.",
    )

    @model_validator(mode="after")
    def max_delay_must_exceed_min(self) -> "JobConfig":
        if self.max_delay < self.min_delay:
            raise ValueError("max_delay must be greater than or equal to min_delay.")
        return self


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class JobCreate(BaseModel):
    """Schema for creating a new scrape job."""

    site_key: str = Field(..., min_length=1, description="Site configuration key (e.g. 'pearls').")
    start_url: str = Field(..., description="URL to begin scraping from.", json_schema_extra={"format": "uri"})
    max_pages: int = Field(10, ge=1, le=500, description="Maximum number of listing pages to scrape.")
    config: JobConfig | None = Field(None, description="Optional runtime configuration overrides.")


# ---------------------------------------------------------------------------
# Logs & URLs
# ---------------------------------------------------------------------------

class JobLogEntry(BaseModel):
    """A single structured log entry emitted during a scrape job."""

    level: Literal["info", "warning", "error"] = Field(..., description="Log severity level.")
    message: str = Field(..., description="Human-readable log message.")
    url: str | None = Field(None, description="URL associated with this log entry, if applicable.")
    timestamp: str | None = Field(None, description="ISO-8601 timestamp of the log entry.")


class JobUrlState(BaseModel):
    """URL discovery state for a scrape job."""

    discovered: list[str] = Field(default_factory=list, description="All discovered listing URLs.")
    visited: list[str] = Field(default_factory=list, description="URLs successfully fetched.")
    failed: list[str] = Field(default_factory=list, description="URLs that failed to fetch.")


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

class JobProgress(BaseModel):
    """Real-time progress counters for a running job."""

    pages_visited: int = Field(0, ge=0, description="Number of pages fetched so far.")
    listings_found: int = Field(0, ge=0, description="Number of listing URLs discovered.")
    listings_scraped: int = Field(0, ge=0, description="Number of listings successfully scraped.")
    errors: int = Field(0, ge=0, description="Number of errors encountered.")


# ---------------------------------------------------------------------------
# Read (detail)
# ---------------------------------------------------------------------------

class JobRead(BaseModel):
    """Full scrape job detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_key: str
    base_url: str | None = None
    start_url: str
    max_pages: int
    status: JobStatus
    progress: JobProgress | None = None
    config: JobConfig | None = None
    logs: list[JobLogEntry] | None = Field(None, description="Structured log entries produced during scraping.")
    urls: JobUrlState | None = Field(None, description="Discovered and visited URL state.")
    error_message: str | None = Field(None, description="Terminal error message when status='failed'.")
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Read (list)
# ---------------------------------------------------------------------------

class JobListRead(BaseModel):
    """Compact scrape job schema for paginated list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_key: str
    status: JobStatus
    progress: JobProgress | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None