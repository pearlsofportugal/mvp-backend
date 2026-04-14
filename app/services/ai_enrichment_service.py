"""AI enrichment service — multi-locale SEO content generation."""
import asyncio
import time
from collections import deque
from threading import Lock
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.gemini_adapter import gemini_adapter
from app.config import settings
from app.core.exceptions import EnrichmentError, NotFoundError
from app.core.logging import get_logger
from app.models.listing_model import Listing
from app.repositories.listings_repository import ListingRepository
from app.schemas.ai_enrichment_schema import (
    BulkEnrichmentItemResult,
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
    EnrichmentSourceStats,
    EnrichmentStats,
    ListingTranslationRequest,
    ListingTranslationResponse,
    LocaleEnrichmentOutput,
    SupportedLocale,
    _ALL_LOCALES,
)

logger = get_logger(__name__)

_AI_REQUEST_TIMESTAMPS = deque()
_AI_RATE_LIMIT_LOCK = Lock()


def _sanitize_keywords(keywords: Sequence[str]) -> list[str]:
    clean = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    # preserve order and uniqueness
    unique: list[str] = []
    seen = set()
    for keyword in clean:
        if keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        unique.append(keyword)
    return unique


def _check_ai_rate_limit(now: float | None = None) -> None:
    """Enforce a simple in-process sliding-window rate limit for AI calls."""
    max_requests = settings.ai_rate_limit_requests
    window_seconds = settings.ai_rate_limit_window
    if max_requests <= 0 or window_seconds <= 0:
        return

    current_time = time.monotonic() if now is None else now
    cutoff = current_time - window_seconds

    with _AI_RATE_LIMIT_LOCK:
        while _AI_REQUEST_TIMESTAMPS and _AI_REQUEST_TIMESTAMPS[0] <= cutoff:
            _AI_REQUEST_TIMESTAMPS.popleft()

        if len(_AI_REQUEST_TIMESTAMPS) >= max_requests:
            raise EnrichmentError(
                f"AI rate limit exceeded: max {max_requests} requests per {window_seconds} seconds."
            )

        _AI_REQUEST_TIMESTAMPS.append(current_time)


def infer_listing_keywords(listing: Listing) -> list[str]:
    """Infer SEO keywords from listing attributes when user does not provide any."""
    derived = [
        listing.property_type,
        listing.typology,
        listing.district,
        listing.county,
        listing.parish,
    ]
    return _sanitize_keywords([part for part in derived if part])


async def bulk_enrich_listings(
    listings: list[Listing],
    request: BulkEnrichmentRequest,
) -> BulkEnrichmentResponse:
    """Enrich a batch of listings concurrently via the multi-locale translations endpoint.

    Each listing is enriched and the result merged into enriched_translations.
    A semaphore limits concurrent AI calls to avoid bursting the Gemini rate limit.
    Callers must commit the session after this function returns.
    """
    _concurrency_limit = asyncio.Semaphore(3)

    async def _enrich_one(listing: Listing) -> BulkEnrichmentItemResult:
        async with _concurrency_limit:
            translate_payload = ListingTranslationRequest(
                listing_id=listing.id,
                locales=request.locales,
                keywords=request.keywords,
                apply=False,
                force=request.force,
            )
            try:
                response = await enrich_listing_translations(listing, translate_payload)

                if not response.locales_generated:
                    return BulkEnrichmentItemResult(
                        listing_id=listing.id,
                        status="skipped",
                        locales_generated=[],
                    )

                # Persist the generated values without re-calling AI.
                apply_payload = ListingTranslationRequest(
                    listing_id=listing.id,
                    locales=request.locales,
                    apply=True,
                    translation_values=response.results,
                )
                await enrich_listing_translations(listing, apply_payload)
                return BulkEnrichmentItemResult(
                    listing_id=listing.id,
                    status="enriched",
                    locales_generated=response.locales_generated,
                )
            except EnrichmentError as exc:
                logger.warning(
                    "Bulk enrichment failed for listing %s: %s",
                    listing.id,
                    exc,
                )
                return BulkEnrichmentItemResult(
                    listing_id=listing.id,
                    status="error",
                    error=str(exc),
                )

    item_results: list[BulkEnrichmentItemResult] = list(
        await asyncio.gather(*[_enrich_one(listing) for listing in listings])
    )
    enriched_count = sum(1 for r in item_results if r.status == "enriched")
    skipped_count = sum(1 for r in item_results if r.status == "skipped")
    failed_count = sum(1 for r in item_results if r.status == "error")

    return BulkEnrichmentResponse(
        total_requested=len(listings),
        enriched=enriched_count,
        skipped=skipped_count,
        failed=failed_count,
        results=item_results,
    )


async def get_enrichment_stats(db: AsyncSession, source_partner: str | None) -> EnrichmentStats:
    """Aggregated enrichment statistics across all listings."""
    _is_enriched = Listing.enriched_translations.isnot(None)

    total_query = select(func.count(Listing.id))
    enriched_query = select(func.count(Listing.id)).where(_is_enriched)

    if source_partner:
        total_query = total_query.where(Listing.source_partner == source_partner)
        enriched_query = enriched_query.where(Listing.source_partner == source_partner)

    total: int = (await db.execute(total_query)).scalar_one()
    enriched: int = (await db.execute(enriched_query)).scalar_one()

    by_source_query = select(
        Listing.source_partner,
        func.count(Listing.id).label("total"),
        func.count(case((_is_enriched, 1))).label("enriched_count"),
    ).group_by(Listing.source_partner)

    if source_partner:
        by_source_query = by_source_query.where(Listing.source_partner == source_partner)

    by_source_rows = (await db.execute(by_source_query)).all()
    by_source = {
        row.source_partner: EnrichmentSourceStats(total=row.total, enriched_count=row.enriched_count)
        for row in by_source_rows
        if row.source_partner is not None
    }

    return EnrichmentStats(
        total_listings=total,
        enriched_count=enriched,
        not_enriched_count=total - enriched,
        enrichment_percentage=round((enriched / total * 100), 2) if total > 0 else 0.0,
        by_source=by_source,
    )


async def get_listings_for_bulk_enrich(db: AsyncSession, payload: BulkEnrichmentRequest) -> list[Listing]:
    """Fetch listings to enrich based on the bulk enrichment request criteria."""
    if payload.listing_ids:
        stmt = select(Listing).where(Listing.id.in_(payload.listing_ids))
    else:
        unenriched_filter = Listing.enriched_translations.is_(None)
        stmt = select(Listing).where(unenriched_filter)
        if payload.source_partner:
            stmt = stmt.where(Listing.source_partner == payload.source_partner)
        stmt = stmt.order_by(Listing.created_at.asc()).limit(payload.limit)

    return (await db.execute(stmt)).scalars().all()


_LOCALE_NAMES: dict[str, str] = {
    "en": "English",
    "pt": "European Portuguese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}

_SYSTEM_INSTRUCTION_MULTILANG = """
You are an expert in Real Estate Copywriting and multilingual SEO.
Your mission is to generate persuasive, search-engine-optimised property descriptions
independently in each requested language, working directly from the original property data.

LANGUAGE RULE (MANDATORY):
Generate each locale's content natively in that language, using the original scraped data
as the factual source. Do NOT translate from English — write each language independently and naturally.

INPUT PROVIDED:
- Original data: {raw_data}
- SEO keywords: {keywords}
- Requested locales: {locales}

WRITING RULES (apply identically to every locale):
- FORMAT: Use exclusively continuous prose in paragraphs. Lists or bullet points are PROHIBITED.
- LENGTH: The description must be strictly between 250 and 400 words.
- REWRITING: Use the source data as a factual reference only. Rewrite with fresh, original copywriting.
- NARRATIVE STRUCTURE:
    1. Introduction: Emotional hook with the location.
    2. Development: Comfort, design, and interior details.
    3. Sustainability/Features: Translate technical specs into tangible benefits.
    4. Closing: Outdoor areas + generic Call-to-Action appropriate for the target language.
- TONE OF VOICE: Professional, inspiring, and modern.
- SEO: Integrate provided keywords naturally. At least one in the first paragraph and one in the closing.
- REAL ESTATE TERMS: Translate Portuguese terms (T1, T2, T3) to the target language equivalent
  (e.g. "1-bedroom apartment" in English, "appartement 1 chambre" in French, "1-Zimmer-Wohnung" in German, "apartamento de 1 dormitorio" in Spanish).
- CONTENT FILTER: Exclude all agency-specific references (names, contacts, taglines).
  The CTA must be generic and culturally appropriate to the target language.

OUTPUT RULES (JSON):
Respond exclusively with valid JSON. Keys are the locale codes requested.
Each locale object must have exactly these three keys: title, description, meta_description.
No markdown, no code blocks, no extra text outside the JSON object.
{
    "pt": {
        "title": "SEO title in European Portuguese (max 60 characters)",
        "description": "Full persuasive prose in European Portuguese (strictly 250-400 words)",
        "meta_description": "Google summary in European Portuguese — EXACTLY 140-155 characters"
    },
    "es": { ... },
    "fr": { ... },
    "de": { ... }
}
Only include locale keys that were requested.
""".strip()


def _build_multilang_prompt(listing: "Listing", keywords: list[str], locales: list[str]) -> str:
    """Build the data prompt for multi-locale generation."""
    locale_labels = ", ".join(f"{loc} ({_LOCALE_NAMES.get(loc, loc)})" for loc in locales)
    sanitized = _sanitize_keywords(keywords)
    kw_str = ", ".join(sanitized) if sanitized else "None provided"

    raw_data_parts = [
        f"Title: {listing.title or ''}",
        f"Property type: {listing.property_type or ''}",
        f"Typology: {listing.typology or ''}",
        f"Bedrooms: {listing.bedrooms or ''}",
        f"Bathrooms: {listing.bathrooms or ''}",
        f"Price: {listing.price_amount or ''} {listing.price_currency or ''}",
        f"Area (useful): {listing.area_useful_m2 or ''} m²",
        f"District: {listing.district or ''}",
        f"County: {listing.county or ''}",
        f"Parish: {listing.parish or ''}",
        f"Energy certificate: {listing.energy_certificate or ''}",
        f"Features: garage={listing.has_garage}, pool={listing.has_pool}, "
        f"elevator={listing.has_elevator}, balcony={listing.has_balcony}",
        f"Description: {listing.description or listing.raw_description or ''}",
    ]
    raw_data = "\n".join(raw_data_parts)

    system = _SYSTEM_INSTRUCTION_MULTILANG.replace("{raw_data}", raw_data).replace(
        "{keywords}", kw_str
    ).replace("{locales}", locale_labels)

    return system


def _call_ai_for_translations(listing: "Listing", keywords: list[str], locales: list[str]) -> dict[str, Any]:
    _check_ai_rate_limit()
    system = _build_multilang_prompt(listing, keywords, locales)
    # The user prompt is minimal — all context is in the system instruction.
    prompt = f"Generate SEO content for the following locales: {', '.join(locales)}"
    return gemini_adapter.generate(system_instruction=system, prompt=prompt)


async def _call_ai_for_translations_async(
    listing: "Listing", keywords: list[str], locales: list[str]
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _call_ai_for_translations, listing, keywords, locales)


def _parse_locale_output(raw: dict[str, Any], locale: str) -> LocaleEnrichmentOutput:
    data = raw.get(locale) or {}
    return LocaleEnrichmentOutput(
        title=data.get("title") or None,
        description=data.get("description") or None,
        meta_description=data.get("meta_description") or None,
    )


async def enrich_listing_translations(
    listing: "Listing",
    payload: ListingTranslationRequest,
) -> ListingTranslationResponse:
    """Generate (or persist) multi-locale SEO content for a listing.

    Two paths:
    - apply=False: call AI for requested locales (respecting force / existing values), return preview.
    - apply=True:  persist caller-supplied translation_values without any AI call.
    """
    requested_locales: list[str] = list(payload.locales)
    keywords_used = _sanitize_keywords(payload.keywords) or infer_listing_keywords(listing)

    # Existing stored translations (may be None or partial dict).
    stored: dict[str, Any] = listing.enriched_translations or {}

    # ------------------------------------------------------------------
    # PATH A — apply=True: persist caller-supplied values, no AI call.
    # ------------------------------------------------------------------
    if payload.apply:
        incoming = payload.translation_values or {}
        merged = dict(stored)
        for locale, locale_output in incoming.items():
            merged[locale] = locale_output.model_dump(exclude_none=True)
        listing.enriched_translations = merged

        results = {
            locale: LocaleEnrichmentOutput.model_validate(merged.get(locale, {}))
            for locale in requested_locales
        }
        return ListingTranslationResponse(
            listing_id=listing.id,
            applied=True,
            model_used=settings.google_genai_model,
            keywords_used=keywords_used,
            locales_generated=list(incoming.keys()),
            locales_cached=[loc for loc in requested_locales if loc not in incoming],
            results=results,
        )

    # ------------------------------------------------------------------
    # PATH B — apply=False: determine which locales need generation.
    # ------------------------------------------------------------------
    locales_to_generate: list[str] = []
    locales_cached: list[str] = []

    for locale in requested_locales:
        existing = stored.get(locale)
        has_content = bool(existing and any(existing.get(f) for f in ("title", "description", "meta_description")))
        if payload.force or not has_content:
            locales_to_generate.append(locale)
        else:
            locales_cached.append(locale)

    results: dict[str, LocaleEnrichmentOutput] = {}

    # Reuse cached locales immediately.
    for locale in locales_cached:
        results[locale] = LocaleEnrichmentOutput.model_validate(stored.get(locale, {}))

    # Generate missing locales in one single AI call.
    if locales_to_generate:
        raw = await _call_ai_for_translations_async(listing, keywords_used, locales_to_generate)
        for locale in locales_to_generate:
            results[locale] = _parse_locale_output(raw, locale)

    return ListingTranslationResponse(
        listing_id=listing.id,
        applied=False,
        model_used=settings.google_genai_model,
        keywords_used=keywords_used,
        locales_generated=locales_to_generate,
        locales_cached=locales_cached,
        results=results,
    )


async def enrich_translations_and_persist(
    db: AsyncSession,
    listing_id: UUID,
    payload: ListingTranslationRequest,
) -> ListingTranslationResponse:
    """Fetch a listing by ID, run translation enrichment, and optionally commit."""
    listing = await ListingRepository.get_listing_by_id(db, listing_id)
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    response = await enrich_listing_translations(listing, payload)
    if payload.apply:
        await db.commit()
    return response
