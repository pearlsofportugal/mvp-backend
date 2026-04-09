"""Pydantic schemas for AI-powered enrichment and SEO endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


SupportedLocale = Literal["en", "pt", "es", "fr", "de"]
_ALL_LOCALES: list[SupportedLocale] = ["en", "pt", "es", "fr", "de"]


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
    """Request to enrich multiple listings in one bulk call."""

    listing_ids: list[UUID] = Field(
        default_factory=list,
        description="Explicit list of listing IDs to enrich. If empty, enrich all unenriched listings.",
    )
    locales: list[SupportedLocale] = Field(
        default_factory=lambda: list(_ALL_LOCALES),
        description="Target locales to generate. Defaults to all supported locales.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Shared SEO keywords. If empty, keywords are inferred per listing.",
    )
    force: bool = Field(
        False,
        description="When True, regenerates locales even if they already have stored translations.",
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
    locales_generated: list[SupportedLocale] = Field(default_factory=list)
    error: str | None = Field(None, description="Error message when status is 'error'.")


class BulkEnrichmentResponse(BaseModel):
    """Aggregated result of a bulk enrichment operation."""

    total_requested: int = Field(..., ge=0)
    enriched: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    results: list[BulkEnrichmentItemResult] = Field(default_factory=list)


class BulkEnrichmentResponse(BaseModel):
    """Aggregated result of a bulk enrichment operation."""

    total_requested: int = Field(..., ge=0)
    enriched: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    results: list[BulkEnrichmentItemResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Multi-locale translation enrichment
# ---------------------------------------------------------------------------

class LocaleEnrichmentOutput(BaseModel):
    """AI-generated SEO content for a single locale."""

    title: str | None = Field(None, description="Optimised listing title.")
    description: str | None = Field(None, description="Enriched listing description.")
    meta_description: str | None = Field(None, description="SEO meta description (≤160 chars recommended).")


class ListingTranslationRequest(BaseModel):
    """Request to generate multi-locale SEO content from original scraped data."""

    listing_id: UUID = Field(..., description="ID of the listing to translate/enrich.")
    locales: list[SupportedLocale] = Field(
        default_factory=lambda: list(_ALL_LOCALES),
        description="Target locales. Defaults to all supported: pt, es, fr, de.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Custom SEO keywords. If empty, keywords are inferred from the listing.",
    )
    force: bool = Field(
        False,
        description="When True, regenerates locales that already have stored translations.",
    )
    apply: bool = Field(
        False,
        description=(
            "When True, persists translation_values to the database. "
            "AI is never called in this mode — translation_values must be supplied."
        ),
    )
    translation_values: dict[str, LocaleEnrichmentOutput] | None = Field(
        None,
        description=(
            "Pre-computed locale outputs to persist. Required when apply=True. "
            "Keys must be valid SupportedLocale codes."
        ),
    )

    @model_validator(mode="after")
    def _validate_locales_not_empty(self) -> "ListingTranslationRequest":
        if not self.locales:
            raise ValueError("At least one locale must be specified.")
        return self

    @model_validator(mode="after")
    def _validate_apply_has_values(self) -> "ListingTranslationRequest":
        if self.apply and not self.translation_values:
            raise ValueError(
                "translation_values must be provided when apply=True. "
                "Call with apply=False first to obtain AI-generated content, "
                "then send it back with apply=True to persist."
            )
        return self


class LocaleTranslationResult(BaseModel):
    """Before/after result for a single locale."""

    locale: SupportedLocale
    output: LocaleEnrichmentOutput
    already_existed: bool = Field(False, description="True when the locale was already stored and force=False was used.")


class ListingTranslationResponse(BaseModel):
    """Response for the multi-locale translation enrichment endpoint."""

    listing_id: UUID
    applied: bool = Field(False, description="True when results were persisted to the database.")
    model_used: str = Field(..., description="Identifier of the AI model used.")
    keywords_used: list[str] = Field(default_factory=list)
    locales_generated: list[SupportedLocale] = Field(
        default_factory=list,
        description="Locales for which new content was generated (not reused from cache).",
    )
    locales_cached: list[SupportedLocale] = Field(
        default_factory=list,
        description="Locales reused from existing stored translations (force=False).",
    )
    results: dict[str, LocaleEnrichmentOutput] = Field(
        default_factory=dict,
        description="Full output keyed by locale code.",
    )