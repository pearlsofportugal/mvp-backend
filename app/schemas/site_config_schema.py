"""Pydantic schemas for SiteConfig API requests and responses."""

import ipaddress
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

_SSRF_BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "127.0.0.1",
    "::1",
    "169.254.169.254",
})


def _validate_no_ssrf(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must use http or https scheme")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("url must contain a valid hostname")
    if hostname in _SSRF_BLOCKED_HOSTS or hostname.endswith((".internal", ".local")):
        raise ValueError("url hostname is not permitted")
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return raw  # domain name — allowed
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        raise ValueError("url must not target private or reserved IP ranges")
    return raw


ExtractionMode = Literal["section", "direct"]
PaginationType = Literal["html_next", "query_param", "incremental_path", "sitemap"]


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
    pagination_type: Literal["html_next", "query_param", "incremental_path", "sitemap"] = Field(
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
    use_js_render: bool = Field(False, description="Use a headless browser (Playwright) to render JavaScript before parsing.")
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
    use_js_render: bool | None = None
    is_active: bool | None = None
    confidence_scores: dict[str, float] | None = Field(None, description="Per-field confidence scores (0.0–1.0).")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

class ConfidenceMeta(BaseModel):
    """Metadata about the last confidence score calculation."""

    job_id: str
    sample_count: int = Field(..., ge=0, description="Number of listings used to calculate scores.")
    updated_at: str = Field(..., description="ISO 8601 timestamp of the last calculation.")


class SiteConfigRead(SiteConfigBase):
    """Full site configuration response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    confidence_meta: ConfidenceMeta | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _extract_confidence_meta(cls, data: Any) -> Any:
        """Split _meta out of confidence_scores JSON into confidence_meta."""
        # Works for both ORM objects (via __dict__) and plain dicts
        if hasattr(data, "__dict__"):
            raw: dict = dict(data.__dict__)
        elif isinstance(data, dict):
            raw = dict(data)
        else:
            return data

        scores: dict = dict(raw.get("confidence_scores") or {})
        meta = scores.pop("_meta", None)

        raw["confidence_scores"] = scores
        if meta:
            raw["confidence_meta"] = ConfidenceMeta.model_validate(meta)

        return raw


class SelectorCandidate(BaseModel):
    """A ranked selector candidate for a target field."""

    selector: str
    sample: str
    score: float = Field(..., ge=0, le=1)


class SiteConfigSuggestRequest(BaseModel):
    """Request payload for selector suggestion."""

    url: AnyHttpUrl

    @field_validator("url", mode="before")
    @classmethod
    def _no_ssrf(cls, v: Any) -> Any:
        _validate_no_ssrf(str(v))
        return v


class SiteConfigSuggestResponse(BaseModel):
    """Suggested selectors grouped by target field."""

    source: Literal["json-ld", "heuristic"]
    candidates: dict[str, list[SelectorCandidate]]


class SiteConfigPreviewRequest(BaseModel):
    """Request payload for selector live preview."""

    url: AnyHttpUrl
    selector: str

    @field_validator("url", mode="before")
    @classmethod
    def _no_ssrf(cls, v: Any) -> Any:
        _validate_no_ssrf(str(v))
        return v


class SiteConfigPreviewResponse(BaseModel):
    """Preview data returned for a selector against a page."""

    matches: int = Field(..., ge=0)
    preview: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Selector validation
# ---------------------------------------------------------------------------

class SelectorValidationResult(BaseModel):
    """Validation result for a single CSS selector."""

    field: str
    selector: str
    valid_css: bool
    matches: int = Field(..., ge=0)
    sample: str | None = None


class SelectorValidationReport(BaseModel):
    """Validation report for a set of CSS selectors run against a live URL."""

    url: str
    success: bool = Field(..., description="False if the page fetch failed or any selector has invalid CSS.")
    results: list[SelectorValidationResult]
    warnings: list[str] = Field(default_factory=list, description="Valid CSS selectors that matched 0 elements.")
    errors: list[str] = Field(default_factory=list, description="Selectors with invalid CSS syntax or a failed page fetch.")


class SelectorValidateRequest(BaseModel):
    """Request payload for selector validation."""

    selectors: dict[str, str] = Field(
        ...,
        description="Mapping of field name → CSS selector to validate.",
    )
    url: AnyHttpUrl | None = Field(
        None,
        description="URL to validate against. Defaults to the site's base_url when called via /{key}/validate-selectors.",
    )


# ---------------------------------------------------------------------------
# Test-scrape
# ---------------------------------------------------------------------------

class TestScrapeRequest(BaseModel):
    """Request payload for test-scrape."""

    url: AnyHttpUrl = Field(..., description="URL of a single listing detail page to test against.")


class TestScrapeNormalized(BaseModel):
    """Normalized fields extracted from the listing page."""

    title: str | None = None
    listing_type: str | None = None
    property_type: str | None = None
    typology: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    price_amount: float | None = None
    price_currency: str | None = None
    price_per_m2: float | None = None
    area_useful_m2: float | None = None
    area_gross_m2: float | None = None
    area_land_m2: float | None = None
    district: str | None = None
    county: str | None = None
    parish: str | None = None
    energy_certificate: str | None = None
    construction_year: int | None = None
    has_garage: bool | None = None
    has_pool: bool | None = None
    has_elevator: bool | None = None
    image_count: int = 0


class TestScrapeResponse(BaseModel):
    """Result of a test-scrape run against a single listing URL."""

    url: str
    success: bool
    raw: dict = Field(default_factory=dict, description="Raw values extracted by the parser before normalisation.")
    normalized: TestScrapeNormalized | None = None
    missing_critical: list[str] = Field(
        default_factory=list,
        description="Critical fields (title, price, property_type, district) absent in the raw output.",
    )
    error: str | None = Field(None, description="Set when the fetch or parse failed entirely.")


# ---------------------------------------------------------------------------
# Test listing page
# ---------------------------------------------------------------------------

class TestListingPageRequest(BaseModel):
    """Request payload for testing a listing/search results page."""

    url: AnyHttpUrl = Field(..., description="URL of a listing/search results page.")
    link_pattern: str | None = Field(
        None,
        description="Regex to test against found links. Overrides the site's saved link_pattern when provided.",
    )
    thumbnail_selector: str | None = Field(
        None,
        description="CSS selector to extract thumbnail images from listing cards. Optional.",
    )


class TestListingPageResponse(BaseModel):
    """Result of a test run against a listing/search results page."""

    url: str
    success: bool
    links_found: int = Field(..., ge=0, description="Total hrefs extracted before applying link_pattern.")
    links_matched: int = Field(..., ge=0, description="Links that matched link_pattern (or all links if no pattern).")
    sample_matched: list[str] = Field(default_factory=list, description="Up to 5 matched listing URLs.")
    sample_rejected: list[str] = Field(default_factory=list, description="Up to 5 URLs rejected by link_pattern.")
    thumbnail_preview: list[str] = Field(default_factory=list, description="Up to 5 thumbnail URLs from thumbnail_selector.")
    next_page_url: str | None = Field(None, description="Next page URL extracted via next_page_selector, if configured.")
    error: str | None = None