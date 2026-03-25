"""Pydantic schemas for SiteConfig API requests and responses."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


ExtractionMode = Literal["section", "direct"]
PaginationType = Literal["html_next", "query_param", "incremental_path"]


# ---------------------------------------------------------------------------
# Base (shared fields)
# ---------------------------------------------------------------------------

class SiteConfigBase(BaseModel):
    """Fields shared across create, update, and read schemas."""

    name: str = Field(..., min_length=1, description="Human-readable site name.")
    base_url: str = Field(..., description="Base URL of the target site (e.g. 'https://example.com').")
    selectors: dict[str, Any] = Field(
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
    pagination_type: Literal["html_next", "query_param", "incremental_path"] = Field(
        "html_next",
        description="Pagination strategy used to move across result pages.",
    )
    pagination_param: str | None = Field(
        None,
        description="Query parameter name used when pagination_type='query_param' (for example 'page').",
    )
    link_pattern: str | None = Field(None, description="Regex pattern to filter listing URLs on listing pages.")
    image_filter: str | None = Field(None, description="Regex to require image URLs to match (include-only filter).")
    image_exclude_filter: str | None = Field(None, description="Regex pattern — images whose URL matches are excluded (e.g. banners, logos).")
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

    name: str | None = Field(None, min_length=1)
    base_url: str | None = None
    selectors: dict[str, Any] | None = None
    extraction_mode: ExtractionMode | None = None
    pagination_type: PaginationType | None = None
    pagination_param: str | None = None
    link_pattern: str | None = None
    image_filter: str | None = None
    image_exclude_filter: str | None = None
    is_active: bool | None = None
    confidence_scores: dict[str, float] | None = Field(None, description="Per-field confidence scores (0.0–1.0).")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

class SiteConfigRead(SiteConfigBase):
    """Full site configuration response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SelectorCandidate(BaseModel):
    """A ranked selector candidate for a target field."""

    selector: str
    sample: str
    score: float = Field(..., ge=0, le=1)


class SiteConfigSuggestRequest(BaseModel):
    """Request payload for selector suggestion."""

    url: AnyHttpUrl


class SiteConfigSuggestResponse(BaseModel):
    """Suggested selectors grouped by target field."""

    source: Literal["json-ld", "heuristic"]
    candidates: dict[str, list[SelectorCandidate]]


class SiteConfigPreviewRequest(BaseModel):
    """Request payload for selector live preview."""

    url: AnyHttpUrl
    selector: str


class SiteConfigPreviewResponse(BaseModel):
    """Preview data returned for a selector against a page."""

    matches: int = Field(..., ge=0)
    preview: list[str] = Field(default_factory=list)