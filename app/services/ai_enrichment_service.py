"""AI enrichment service for SEO title/description/meta_description generation."""
import asyncio
import json
import time
from collections import deque
from threading import Lock
from typing import Any, Sequence

from app.config import settings
from app.core.exceptions import EnrichmentError
from app.core.logging import get_logger
from app.models.listing_model import Listing
from app.schemas.ai_enrichment_schema import (
    AIEnrichmentFieldResult,
    AIEnrichmentOutput,
    AIListingEnrichmentRequest,
    AIListingEnrichmentResponse,
    AITextOptimizationResponse,
    BulkEnrichmentItemResult,
    BulkEnrichmentRequest,
    BulkEnrichmentResponse,
)

logger = get_logger(__name__)

_AI_REQUEST_TIMESTAMPS = deque()
_AI_RATE_LIMIT_LOCK = Lock()

_SYSTEM_INSTRUCTION_SEO = """
You are an expert in Real Estate Copywriting and SEO.
Your mission is to transform technical property descriptions into persuasive, sales-driven texts that are fully optimized for search engines.

LANGUAGE RULE (MANDATORY):
You must ALWAYS respond in English, regardless of the language of the input.
If the technical description, keywords, or any other input is provided in Portuguese or any other language, translate and process everything internally and respond exclusively in English.

INPUT PROVIDED:
- Technical description: {raw_description}
- SEO keywords: {keywords}
- Property type: {property_type}
- Location: {location}

WRITING RULES:
- FORMAT: Use exclusively continuous prose organized in paragraphs. The use of lists or bullet points is strictly PROHIBITED.
- LENGTH: The description must be strictly between 250 and 400 words. If the source text is longer, summarize — do not translate verbatim.
- REWRITING: Do NOT translate the source text verbatim. Use it only as a factual reference. Rewrite entirely with fresh, original copywriting in English.
- NARRATIVE STRUCTURE:
    1. Introduction: Strong emotional hook with a clear mention of the location.
    2. Development: Focus on comfort, design, and interior details.
    3. Sustainability: Dedicated section translating technical specs (e.g. solar panels, insulation, energy efficiency) into tangible benefits (savings, thermal comfort, lower bills).
    4. Closing: Outdoor areas followed by a generic Call-to-Action (CTA).
- TONE OF VOICE: Professional, inspiring, and modern.
- SEO: Integrate the provided keywords naturally into the text. Include at least one keyword in the first paragraph and one in the closing. Maximum 3 repetitions per keyword.
- KEYWORDS TRANSLATION: If any keyword is a Portuguese real estate term (e.g. T1, T2, T3), translate it to its English equivalent (e.g. 1-bedroom, 2-bedroom, 3-bedroom) before integrating it into the text.
- CONTENT FILTER: Ignore and exclude any agency-specific references found in the source text,
  including but not limited to: agency names, contact invitations tied to a specific agency
  (e.g. "Contact XYZ Imobiliária", "Call us at...", "Visit our website"),
  promotional taglines, and agent names.
  The closing CTA must be generic (e.g. "Schedule a viewing today", "Enquire now").

OUTPUT RULES (JSON):
Respond exclusively with valid JSON using the following keys — no markdown, no code blocks, no extra text.
All JSON values must be written in English, even if the input was in another language:
{
    "title": "SEO title in English (max 60 characters)",
    "description": "Full persuasive prose text in English (strictly 250-400 words)",
    "meta_description": "Google summary — EXACTLY 140-155 characters, no more, no less"
}
""".strip()

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


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.replace("json", "", 1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


def _build_prompt(content: str, keywords: Sequence[str]) -> str:
    """Cria o prompt de dados (User Prompt) com o conteúdo e keywords."""
    sanitized = _sanitize_keywords(keywords)
    primary = sanitized[0] if sanitized else "Imóvel"
    secondary = ", ".join(sanitized[1:]) if len(sanitized) > 1 else "Nenhuma"
    
    return f"""
PALAVRA-CHAVE PRINCIPAL: {primary}
PALAVRAS-CHAVE SECUNDÁRIAS: {secondary}

CONTEÚDO DO IMÓVEL A PROCESSAR:
{content}
""".strip()
_client: Any = None


def _get_client():
    global _client
    if _client is None:
        if not settings.google_genai_api_key:
            raise EnrichmentError("google_genai_api_key is not configured")
        try:
            from google import genai
            _client = genai.Client(api_key=settings.google_genai_api_key)
        except Exception as exc:
            raise EnrichmentError("google-genai dependency is not available", detail=str(exc)) from exc
    return _client


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

def _call_ai_for_seo(content: str, keywords: Sequence[str]) -> dict[str, Any]:
    _check_ai_rate_limit()
    client = _get_client()
    prompt = _build_prompt(content, keywords)
    
    try:
        response = client.models.generate_content(
            model=settings.google_genai_model,
            config={
                "system_instruction": _SYSTEM_INSTRUCTION_SEO,
                "temperature": settings.google_genai_temperature,
                "response_mime_type": "application/json",
            },
            contents=prompt, # Dados do imóvel
        )
        
        # Como usamos response_mime_type, o Gemini já deve retornar JSON puro
        return _extract_json(str(response.text))
        
    except Exception as exc:
        logger.exception("AI SEO generation failed")
        raise EnrichmentError("Failed to generate AI SEO output", detail=str(exc)) from exc

async def _call_ai_for_seo_async(content: str, keywords: Sequence[str]) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _call_ai_for_seo, content, keywords)

def _normalize_output(payload: dict[str, Any]) -> AIEnrichmentOutput:
    return AIEnrichmentOutput(
        title=(payload.get("title") or payload.get("meta_title") or None),
        description=(payload.get("description") or payload.get("enriched_description") or None),
        meta_description=payload.get("meta_description") or None,
    )


def optimize_text_with_ai(content: str, keywords: Sequence[str]) -> AITextOptimizationResponse:
    """AI optimization equivalent to previous `otimizar_para_seo` function."""
    sanitized_keywords = _sanitize_keywords(keywords)
    raw = _call_ai_for_seo(content, sanitized_keywords)
    output = _normalize_output(raw)
    return AITextOptimizationResponse(
        model_used=settings.google_genai_model,
        keywords_used=sanitized_keywords,
        output=output,
    )


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


async def enrich_listing_with_ai(listing: Listing, payload: AIListingEnrichmentRequest) -> AIListingEnrichmentResponse:
    """Apply AI SEO enrichment to selected listing fields (preview or apply)."""
    fields = payload.fields or ["title", "description", "meta_description"]

    # original_values: what the user sees as "before" — always the scraped/clean source,
    # never a previously-enriched value (prevents showing AI output as the baseline).
    original_values = {
        "title": listing.title,
        "description": listing.description or listing.raw_description,
        "meta_description": listing.meta_description,
    }

    # destination_current_values: the AI-specific fields that guard against
    # overwriting on repeated calls without force=True.
    destination_current_values = {
        "title": listing.enriched_title,
        "description": listing.enriched_description,
        "meta_description": listing.enriched_meta_description,
    }

    keywords_used = _sanitize_keywords(payload.keywords) or infer_listing_keywords(listing)

    # Determine which fields actually need enriching before spending API quota.
    fields_needing_enrichment = [
        field for field in fields
        if payload.force or not bool(
            destination_current_values.get(field) and
            str(destination_current_values[field]).strip()
        )
    ]

    if not fields_needing_enrichment:
        # All requested fields already have AI values and force=False — skip the API call.
        results = [
            AIEnrichmentFieldResult(
                field=field,
                original=original_values.get(field),
                enriched=destination_current_values.get(field),
                changed=False,
            )
            for field in fields
        ]
        return AIListingEnrichmentResponse(
            listing_id=listing.id,
            applied=payload.apply,
            model_used=settings.google_genai_model,
            keywords_used=keywords_used,
            results=results,
        )

    # Always use the original scraped/cleaned content as AI input to avoid
    # enrichment drift (AI re-enriching its own previous output on force=True).
    source_content = "\n\n".join(
        [
            f"Título atual: {listing.title or ''}",
            f"Descrição atual: {(listing.description or listing.raw_description or '')}",
            f"Meta descrição atual: {listing.meta_description or ''}",
        ]
    ).strip()

    raw = await _call_ai_for_seo_async(source_content, keywords_used)
    output = _normalize_output(raw)

    field_value_map = {
        "title": output.title,
        "description": output.description,
        "meta_description": output.meta_description,
    }

    results: list[AIEnrichmentFieldResult] = []
    for field in fields:
        original = original_values.get(field)
        enriched = field_value_map.get(field)
        destination_current = destination_current_values.get(field)

        already_has_value = bool(destination_current and destination_current.strip())
        skip = not payload.force and already_has_value

        changed = not skip and (enriched or "") != (original or "")
        results.append(AIEnrichmentFieldResult(
            field=field,
            original=original,
            enriched=enriched if not skip else destination_current,
            changed=changed,
        ))

        if payload.apply and changed:
            if field == "title":
                listing.enriched_title = enriched
            elif field == "description":
                listing.enriched_description = enriched
            elif field == "meta_description":
                listing.enriched_meta_description = enriched

    return AIListingEnrichmentResponse(
        listing_id=listing.id,
        applied=payload.apply,
        model_used=settings.google_genai_model,
        keywords_used=keywords_used,
        results=results,
    )


async def bulk_enrich_listings(
    listings: list[Listing],
    request: BulkEnrichmentRequest,
) -> BulkEnrichmentResponse:
    """Enrich a batch of listings sequentially, respecting the rate limit.

    Each listing is processed with apply=True so callers only need to commit
    once after this function returns. Failed listings are recorded but do not
    abort the rest of the batch.
    """
    item_results: list[BulkEnrichmentItemResult] = []
    enriched_count = 0
    skipped_count = 0
    failed_count = 0

    for listing in listings:
        per_listing_payload = AIListingEnrichmentRequest(
            listing_id=listing.id,
            fields=request.fields or [],
            keywords=request.keywords,
            apply=True,
            force=request.force,
        )
        try:
            response = await enrich_listing_with_ai(listing, per_listing_payload)
            fields_changed = [r.field for r in response.results if r.changed]
            if fields_changed:
                item_results.append(BulkEnrichmentItemResult(
                    listing_id=listing.id,
                    status="enriched",
                    fields_changed=fields_changed,
                ))
                enriched_count += 1
            else:
                item_results.append(BulkEnrichmentItemResult(
                    listing_id=listing.id,
                    status="skipped",
                ))
                skipped_count += 1
        except EnrichmentError as exc:
            logger.warning(
                "Bulk enrichment failed for listing %s: %s",
                listing.id,
                exc,
            )
            item_results.append(BulkEnrichmentItemResult(
                listing_id=listing.id,
                status="error",
                error=str(exc),
            ))
            failed_count += 1

    return BulkEnrichmentResponse(
        total_requested=len(listings),
        enriched=enriched_count,
        skipped=skipped_count,
        failed=failed_count,
        results=item_results,
    )
