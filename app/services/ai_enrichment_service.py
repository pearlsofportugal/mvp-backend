"""AI enrichment service for SEO title/description/meta_description generation."""
import json
from typing import Any, Dict, List, Sequence

from app.config import settings
from app.core.exceptions import EnrichmentError
from app.core.logging import get_logger
from app.models.listing import Listing
from app.schemas.ai_enrichment import (
    AIEnrichmentFieldResult,
    AIEnrichmentOutput,
    AIListingEnrichmentRequest,
    AIListingEnrichmentResponse,
    AITextOptimizationResponse,
)

logger = get_logger(__name__)

_SYSTEM_INSTRUCTION_SEO = """
Atua como um Especialista em Copywriting Imobiliário e SEO. 
Sua missão é transformar descrições técnicas em textos de venda persuasivos e otimizados.

REGRAS DE ESCRITA:
- FORMATO: Utilize exclusivamente texto corrido organizado em parágrafos. É expressamente PROIBIDO o uso de listas ou bullet points.
- ESTRUTURA NARRATIVA:
    1. Introdução: Gancho emocional forte e menção à localização.
    2. Desenvolvimento: Foco no conforto, design e detalhes interiores.
    3. Sustentabilidade: Secção dedicada a especificações técnicas (ex: painéis solares, isolamento, eficiência) traduzidas em benefícios (poupança, conforto térmico).
    4. Encerramento: Áreas exteriores e um Apelo à Ação (CTA) direto.
- TOM DE VOZ: Profissional, inspirador e moderno.
- SEO: Integre as palavras-chave fornecidas de forma fluida e natural no texto.

REGRAS DE OUTPUT (JSON):
Responda obrigatoriamente em JSON válido com as seguintes chaves:
{
  "title": "Título SEO (máx 60 chars)",
  "description": "O texto corrido persuasivo e completo",
  "meta_description": "Resumo para Google (140-155 chars)"
}
""".strip()


def _sanitize_keywords(keywords: Sequence[str]) -> List[str]:
    clean = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    # preserve order and uniqueness
    unique: List[str] = []
    seen = set()
    for keyword in clean:
        if keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        unique.append(keyword)
    return unique


def _extract_json(text: str) -> Dict[str, Any]:
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

import asyncio

def _call_ai_for_seo(content: str, keywords: Sequence[str]) -> Dict[str, Any]:
    client = _get_client()
    prompt = _build_prompt(content, keywords)
    
    try:
        response = client.models.generate_content(
            model=settings.google_genai_model,
            config={
                "system_instruction": _SYSTEM_INSTRUCTION_SEO, # Persona e Regras
                "temperature": 0.7, # Equilíbrio entre criatividade e precisão
                "response_mime_type": "application/json",
            },
            contents=prompt, # Dados do imóvel
        )
        
        # Como usamos response_mime_type, o Gemini já deve retornar JSON puro
        return _extract_json(str(response.text))
        
    except Exception as exc:
        logger.exception("AI SEO generation failed")
        raise EnrichmentError("Failed to generate AI SEO output", detail=str(exc)) from exc

async def _call_ai_for_seo_async(content: str, keywords: Sequence[str]) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_ai_for_seo, content, keywords)

def _normalize_output(payload: Dict[str, Any]) -> AIEnrichmentOutput:
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


def infer_listing_keywords(listing: Listing) -> List[str]:
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
    original_values = {
        "title": listing.title,
        "description": listing.enriched_description or listing.description or listing.raw_description,
        "meta_description": listing.meta_description,
    }
    destination_current_values = {
        "title": listing.title,
        "description": listing.enriched_description,
        "meta_description": listing.meta_description,
    }

    keywords_used = _sanitize_keywords(payload.keywords) or infer_listing_keywords(listing)

    source_content = "\n\n".join(
        [
            f"Título atual: {listing.title or ''}",
            f"Descrição atual: {(listing.enriched_description or listing.description or listing.raw_description or '')}",
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

    results: List[AIEnrichmentFieldResult] = []
    for field in fields:
        original = original_values.get(field)
        enriched = field_value_map.get(field)
        destination_current = destination_current_values.get(field)

        already_has_value = bool(destination_current and destination_current.strip())
        skip = not payload.force and already_has_value

        enriched_value = field_value_map.get(field)
        changed = not skip and (enriched_value or "") != (original or "")
        results.append(AIEnrichmentFieldResult(
            field=field,
            original=original,
            enriched=enriched_value if not skip else None,
            changed=changed,
        ))

        if payload.apply and changed:
            if field == "title":
                listing.title = enriched
            elif field == "description":
                listing.enriched_description = enriched
            elif field == "meta_description":
                listing.meta_description = enriched

    return AIListingEnrichmentResponse(
        listing_id=listing.id,
        applied=payload.apply,
        model_used=settings.google_genai_model,
        keywords_used=keywords_used,
        results=results,
    )


