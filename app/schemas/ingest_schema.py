"""Schemas for the /api/v1/ingest endpoint — extract one listing from any URL."""
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.schemas.property_schema import PropertySchema
from app.schemas.site_config_schema import _validate_no_ssrf


class IngestRequest(BaseModel):
    """Request payload for a single-URL ingest."""

    url: AnyHttpUrl = Field(..., description="URL of a single property listing page.")
    persist: bool = Field(
        False,
        description="Reserved — not yet implemented. The caller is expected to "
        "persist the returned data on its own side.",
    )

    @field_validator("url", mode="before")
    @classmethod
    def _no_ssrf(cls, v: object) -> object:
        _validate_no_ssrf(str(v))
        return v


class IngestCompleteness(BaseModel):
    """Which listing fields came through and which the caller needs to fill in."""

    populated: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class IngestResponse(BaseModel):
    """Result of an ingest run."""

    url: str
    success: bool
    source: Literal["partner_config", "generic", "ego_platform"] | None = None
    property: PropertySchema | None = None
    raw: dict | None = Field(
        None,
        description="The raw parser output before normalization — useful as a "
        "fallback for fields the normalizer drops, and for debugging.",
    )
    completeness: IngestCompleteness | None = None
    field_provenance: dict[str, str | None] | None = Field(
        None,
        description="Which extraction layer supplied each field "
        "(structured_data / suggester / heuristic / llm / partner_config).",
    )
    error: str | None = None
