"""Pydantic schemas for AI-powered enrichment and SEO endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


AIEnrichmentTargetField = Literal["title", "description", "meta_description"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class AITextOptimizationRequest(BaseModel):
    """Optimize free text using AI with optional SEO keyword guidance."""

    content: str = Field(..., min_length=5, description="Original text content to optimize.")
    keywords: list[str] = Field(
        default_factory=list,
        description="SEO keywords; the first entry is treated as the primary keyword.",
    )
    fields: list[AIEnrichmentTargetField] = Field(
        default_factory=lambda: ["title", "description", "meta_description"],
        description="Output fields to generate. Defaults to all three.",
    )

    @model_validator(mode="after")
    def fields_must_not_be_empty(self) -> "AITextOptimizationRequest":
        if not self.fields:
            raise ValueError("At least one target field must be specified.")
        return self


class AIListingEnrichmentRequest(BaseModel):
    """AI enrichment request targeting an existing listing by ID."""

    listing_id: UUID = Field(..., description="ID of the listing to enrich.")
    fields: list[AIEnrichmentTargetField] = Field(
        default_factory=list,
        description="Listing fields to enrich. If empty, all fields are enriched.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Custom SEO keywords. If empty, keywords are inferred from the listing.",
    )
    apply: bool = Field(
        False,
        description="When True, persists AI results to the database; otherwise returns a preview.",
    )
    force: bool = Field(
        False,
        description="When True, regenerates output even if target fields already have values.",
    )


# ---------------------------------------------------------------------------
# Output / results
# ---------------------------------------------------------------------------

class AIEnrichmentOutput(BaseModel):
    """AI-generated SEO content for a listing."""

    title: str | None = Field(None, description="Optimised listing title.")
    description: str | None = Field(None, description="Enriched listing description.")
    meta_description: str | None = Field(None, description="SEO meta description (≤160 chars recommended).")


class AIEnrichmentFieldResult(BaseModel):
    """Before/after comparison for a single enriched field."""

    field: AIEnrichmentTargetField = Field(..., description="The field that was enriched.")
    original: str | None = Field(None, description="Value before enrichment.")
    enriched: str | None = Field(None, description="Value after enrichment.")
    changed: bool = Field(False, description="True when the enriched value differs from the original.")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class AITextOptimizationResponse(BaseModel):
    """Response for the free-text AI optimisation endpoint."""

    model_used: str = Field(..., description="Identifier of the AI model used (e.g. 'gpt-4o').")
    keywords_used: list[str] = Field(default_factory=list, description="Keywords that guided the optimisation.")
    output: AIEnrichmentOutput


class AIListingEnrichmentResponse(BaseModel):
    """Response for the listing AI enrichment endpoint."""

    listing_id: UUID
    applied: bool = Field(False, description="True when results were persisted to the database.")
    model_used: str = Field(..., description="Identifier of the AI model used.")
    keywords_used: list[str] = Field(default_factory=list, description="Keywords that guided the enrichment.")
    results: list[AIEnrichmentFieldResult] = Field(default_factory=list, description="Per-field before/after results.")


class EnrichmentPreview(BaseModel):
    """Preview of AI enrichment for a listing (all three SEO fields)."""

    original_title: str | None = Field(None, description="Title before enrichment.")
    enriched_title: str | None = Field(None, description="Title after enrichment.")
    original_description: str | None = Field(None, description="Description before enrichment.")
    enriched_description: str | None = Field(None, description="Description after enrichment.")
    original_meta_description: str | None = Field(None, description="Meta description before enrichment.")
    enriched_meta_description: str | None = Field(None, description="Meta description after enrichment.")
    model_used: str = Field(..., description="Identifier of the AI model used.")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class EnrichmentSourceStats(BaseModel):
    """Enrichment counts for a single source partner."""

    total: int = Field(..., ge=0, description="Total listings from this source.")
    enriched_count: int = Field(..., ge=0, description="Listings with at least one AI-enriched field.")

    @model_validator(mode="after")
    def enriched_cannot_exceed_total(self) -> "EnrichmentSourceStats":
        if self.enriched_count > self.total:
            raise ValueError("enriched_count cannot exceed total.")
        return self


class EnrichmentStats(BaseModel):
    """Aggregated enrichment statistics across all listings."""

    total_listings: int = Field(..., ge=0)
    enriched_count: int = Field(..., ge=0)
    not_enriched_count: int = Field(..., ge=0)
    enrichment_percentage: float = Field(..., ge=0.0, le=100.0, description="Percentage of listings enriched.")
    by_source: dict[str, EnrichmentSourceStats] = Field(
        default_factory=dict,
        description="Per-source breakdown keyed by source partner slug.",
    )

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> "EnrichmentStats":
        if self.enriched_count + self.not_enriched_count != self.total_listings:
            raise ValueError("enriched_count + not_enriched_count must equal total_listings.")
        return self


# ---------------------------------------------------------------------------
# Bulk enrichment
# ---------------------------------------------------------------------------

class BulkEnrichmentRequest(BaseModel):
    """Request to enrich multiple listings in one call."""

    listing_ids: list[UUID] = Field(
        default_factory=list,
        description="Explicit list of listing IDs to enrich. If empty, enrich all unenriched listings.",
    )
    fields: list[AIEnrichmentTargetField] = Field(
        default_factory=list,
        description="Fields to enrich. Defaults to all three.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Shared SEO keywords. If empty, keywords are inferred per listing.",
    )
    force: bool = Field(
        False,
        description="When True, regenerates output even if target fields already have values.",
    )
    source_partner: str | None = Field(
        None,
        description="When set (and listing_ids is empty), restricts bulk enrichment to this partner.",
    )
    limit: int = Field(
        50,
        ge=1,
        le=200,
        description="Maximum number of listings to process in this call.",
    )


class BulkEnrichmentItemResult(BaseModel):
    """Per-listing result within a bulk enrichment response."""

    listing_id: UUID
    status: str = Field(..., description="'enriched', 'skipped', or 'error'")
    fields_changed: list[AIEnrichmentTargetField] = Field(default_factory=list)
    error: str | None = Field(None, description="Error message when status is 'error'.")


class BulkEnrichmentResponse(BaseModel):
    """Aggregated result of a bulk enrichment operation."""

    total_requested: int = Field(..., ge=0)
    enriched: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    results: list[BulkEnrichmentItemResult] = Field(default_factory=list)