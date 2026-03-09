"""Pydantic schemas for AI-powered enrichment and SEO endpoints."""

from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


AIEnrichmentTargetField = Literal["title", "description", "meta_description"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class AITextOptimizationRequest(BaseModel):
    """Optimize free text using AI with optional SEO keyword guidance."""

    content: str = Field(..., min_length=5, description="Original text content to optimize.")
    keywords: List[str] = Field(
        default_factory=list,
        description="SEO keywords; the first entry is treated as the primary keyword.",
    )
    fields: List[AIEnrichmentTargetField] = Field(
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
    fields: List[AIEnrichmentTargetField] = Field(
        default_factory=list,
        description="Listing fields to enrich. If empty, all fields are enriched.",
    )
    keywords: List[str] = Field(
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

    title: Optional[str] = Field(None, description="Optimised listing title.")
    description: Optional[str] = Field(None, description="Enriched listing description.")
    meta_description: Optional[str] = Field(None, description="SEO meta description (≤160 chars recommended).")


class AIEnrichmentFieldResult(BaseModel):
    """Before/after comparison for a single enriched field."""

    field: AIEnrichmentTargetField = Field(..., description="The field that was enriched.")
    original: Optional[str] = Field(None, description="Value before enrichment.")
    enriched: Optional[str] = Field(None, description="Value after enrichment.")
    changed: bool = Field(False, description="True when the enriched value differs from the original.")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class AITextOptimizationResponse(BaseModel):
    """Response for the free-text AI optimisation endpoint."""

    model_used: str = Field(..., description="Identifier of the AI model used (e.g. 'gpt-4o').")
    keywords_used: List[str] = Field(default_factory=list, description="Keywords that guided the optimisation.")
    output: AIEnrichmentOutput


class AIListingEnrichmentResponse(BaseModel):
    """Response for the listing AI enrichment endpoint."""

    listing_id: UUID
    applied: bool = Field(False, description="True when results were persisted to the database.")
    model_used: str = Field(..., description="Identifier of the AI model used.")
    keywords_used: List[str] = Field(default_factory=list, description="Keywords that guided the enrichment.")
    results: List[AIEnrichmentFieldResult] = Field(default_factory=list, description="Per-field before/after results.")


class EnrichmentPreview(BaseModel):
    """Preview of AI enrichment for a single listing (description only)."""

    original_description: Optional[str] = Field(None, description="Description before enrichment.")
    enriched_description: Optional[str] = Field(None, description="Description after enrichment.")
    model_used: str = Field(..., description="Identifier of the AI model used.")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class EnrichmentSourceStats(BaseModel):
    """Enrichment counts for a single source partner."""

    total: int = Field(..., ge=0, description="Total listings from this source.")
    enriched: int = Field(..., ge=0, description="Listings with enriched descriptions.")

    @model_validator(mode="after")
    def enriched_cannot_exceed_total(self) -> "EnrichmentSourceStats":
        if self.enriched > self.total:
            raise ValueError("enriched cannot exceed total.")
        return self


class EnrichmentStats(BaseModel):
    """Aggregated enrichment statistics across all listings."""

    total_listings: int = Field(..., ge=0)
    enriched_count: int = Field(..., ge=0)
    not_enriched_count: int = Field(..., ge=0)
    enrichment_percentage: float = Field(..., ge=0.0, le=100.0, description="Percentage of listings enriched.")
    by_source: Dict[str, EnrichmentSourceStats] = Field(
        default_factory=dict,
        description="Per-source breakdown keyed by source partner slug.",
    )

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> "EnrichmentStats":
        if self.enriched_count + self.not_enriched_count != self.total_listings:
            raise ValueError("enriched_count + not_enriched_count must equal total_listings.")
        return self