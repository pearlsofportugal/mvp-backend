"""Pydantic schemas for the site preview / test endpoints."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class PreviewListingRequest(BaseModel):
    """Request body for POST /api/v1/sites/preview/listing.

    Allows testing CSS selectors against a live listing detail page
    without persisting any data.
    """

    url: str = Field(..., description="URL of the listing detail page to test.")
    selectors: Dict[str, Any] = Field(..., description="CSS selectors to evaluate against the page.")
    extraction_mode: Literal["section", "direct"] = Field(
        "section",
        description="Extraction strategy: 'section' (name/value pairs) or 'direct' (CSS selectors).",
    )
    base_url: str = Field(..., description="Base URL of the site, used to resolve relative links.")
    image_filter: Optional[str] = Field(None, description="Regex pattern to filter image URLs.")


class PreviewListingPageRequest(BaseModel):
    """Request body for POST /api/v1/sites/preview/listing-page.

    Allows testing link discovery against a live listing index page.
    """

    url: str = Field(..., description="URL of the listing index page to test.")
    selectors: Dict[str, Any] = Field(..., description="CSS selectors to evaluate against the page.")
    base_url: str = Field(..., description="Base URL of the site, used to resolve relative links.")
    link_pattern: Optional[str] = Field(None, description="Regex pattern to filter discovered listing URLs.")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class FieldPreviewResult(BaseModel):
    """Extraction result for a single field."""

    field: str = Field(..., description="Field name as specified in the selectors map.")
    raw_value: Optional[str] = Field(None, description="Raw extracted value before any mapping.")
    mapped_to: Optional[str] = Field(None, description="Canonical field name this value maps to.")
    status: Literal["ok", "empty", "error"] = Field(..., description="Extraction outcome.")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class PreviewListingResponse(BaseModel):
    """Response for the listing detail preview endpoint."""

    url: str = Field(..., description="The URL that was tested.")
    extraction_mode: Literal["section", "direct"] = Field(..., description="Extraction mode used.")
    fields: List[FieldPreviewResult] = Field(default_factory=list, description="Per-field extraction results.")
    images_found: int = Field(..., ge=0, description="Number of images extracted after applying the filter.")
    raw_data: Dict[str, Any] = Field(default_factory=dict, description="Raw extracted data keyed by field name.")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal issues encountered during extraction.")


class PreviewListingPageResponse(BaseModel):
    """Response for the listing index page preview endpoint."""

    url: str = Field(..., description="The URL that was tested.")
    links_found: int = Field(..., ge=0, description="Total number of listing links discovered.")
    sample_links: List[str] = Field(default_factory=list, description="Up to 10 sample discovered links.")
    next_page_url: Optional[str] = Field(None, description="URL of the next page, if detected.")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal issues encountered during extraction.")