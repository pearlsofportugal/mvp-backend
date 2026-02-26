"""Preview service — tests selectors against a live URL without persisting data.

Used by POST /api/v1/sites/preview/* to validate site configuration
before running a full scrape job.

CORREÇÕES v2:
- parse_listing_page agora é async — adicionado `await` (bug crítico: retornava coroutine não executada)
"""
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.logging import get_logger
from app.services.ethics_service import EthicalScraper
from app.services.parser_service import (
    parse_listing_links,
    parse_listing_page,
    parse_next_page,
)

from app.schemas.preview_schema import (
    FieldPreviewResult,
    PreviewListingResponse,
    PreviewListingPageResponse,
)
logger = get_logger(__name__)

CANONICAL_FIELDS = [
    ("title", "title"),
    ("property_id", "partner_id"),
    ("price", "price_amount"),
    ("business_type", "listing_type"),
    ("property_type", "property_type"),
    ("typology", "typology"),
    ("bedrooms", "bedrooms"),
    ("bathrooms", "bathrooms"),
    ("district", "district"),
    ("county", "county"),
    ("parish", "parish"),
    ("useful_area", "area_useful_m2"),
    ("gross_area", "area_gross_m2"),
    ("land_area", "area_land_m2"),
    ("energy_certificate", "energy_certificate"),
    ("construction_year", "construction_year"),
    ("garage", "has_garage"),
    ("elevator", "has_elevator"),
    ("balcony", "has_balcony"),
    ("swimming_pool", "has_pool"),
    ("raw_description", "raw_description"),
]


def _build_field_results(raw_data: Dict[str, Any]) -> Tuple[List[FieldPreviewResult], List[str]]:
    """Map raw parser output to canonical field results with status."""
    results = []
    warnings = []

    for raw_key, canonical in CANONICAL_FIELDS:
        raw_value = raw_data.get(raw_key)
        if raw_value is None:
            raw_value = raw_data.get(canonical)

        if raw_value is not None and str(raw_value).strip():
            status = "ok"
            display_value = str(raw_value)[:200]
        else:
            status = "empty"
            display_value = None
            if raw_key in ("title", "price", "district"):
                warnings.append(f"Campo crítico '{raw_key}' não foi extraído — verifica o seletor.")

        results.append(FieldPreviewResult(
            field=raw_key,
            raw_value=display_value,
            mapped_to=canonical,
            status=status,
        ))

    known_raw_keys = {r for r, _ in CANONICAL_FIELDS} | {
        "url", "images", "alt_texts", "page_title", "meta_description", "headers"
    }
    for k, v in raw_data.items():
        if k not in known_raw_keys and v:
            results.append(FieldPreviewResult(
                field=k,
                raw_value=str(v)[:200],
                mapped_to=None,
                status="ok",
            ))

    return results, warnings


async def preview_listing_detail(
    url: str,
    selectors: Dict[str, Any],
    extraction_mode: str,
    base_url: str,
    image_filter: Optional[str] = None,
) -> PreviewListingResponse:
    """Fetch a listing detail page and run the parser against it.

    Returns field-by-field results showing what was extracted and what was missed.
    Does NOT persist anything to the database.
    """
    scraper = EthicalScraper(
        min_delay=0,
        max_delay=0,
        user_agent=settings.default_user_agent,
        timeout=30,
    )

    full_selectors = {**selectors}
    if image_filter:
        full_selectors["image_filter"] = image_filter

    warnings: List[str] = []

    try:
        response = await asyncio.to_thread(scraper.get, url)
        if not response:
            return PreviewListingResponse(
                url=url,
                extraction_mode=extraction_mode,
                fields=[],
                images_found=0,
                raw_data={},
                warnings=[
                    f"Não foi possível aceder à URL: {url}. "
                    "Verifica se o site está acessível e se o robots.txt permite scraping."
                ],
            )

        raw_data = await parse_listing_page(
            response.text,
            url,
            full_selectors,
            extraction_mode,
        )

        field_results, field_warnings = _build_field_results(raw_data)
        warnings.extend(field_warnings)

        images = raw_data.get("images", [])
        if not images:
            warnings.append(
                "Nenhuma imagem encontrada. Verifica o 'image_selector' e o 'image_filter'."
            )

        return PreviewListingResponse(
            url=url,
            extraction_mode=extraction_mode,
            fields=field_results,
            images_found=len(images),
            raw_data={k: v for k, v in raw_data.items()
                      if k not in ("images", "alt_texts", "headers")},
            warnings=warnings,
        )

    except Exception as e:
        logger.error("Preview failed for %s: %s", url, str(e))
        return PreviewListingResponse(
            url=url,
            extraction_mode=extraction_mode,
            fields=[],
            images_found=0,
            raw_data={},
            warnings=[f"Erro ao fazer preview: {str(e)}"],
        )
    finally:
        scraper.close()


async def preview_listing_page(
    url: str,
    selectors: Dict[str, Any],
    base_url: str,
    link_pattern: Optional[str] = None,
) -> PreviewListingPageResponse:
    """Fetch a listing/search page and test link extraction + pagination.

    Returns the links found and next page URL.
    Does NOT persist anything to the database.
    """
    scraper = EthicalScraper(
        min_delay=0,
        max_delay=0,
        user_agent=settings.default_user_agent,
        timeout=30,
    )

    full_selectors = {**selectors}
    if link_pattern:
        full_selectors["listing_link_pattern"] = link_pattern

    warnings: List[str] = []

    try:
        response = await asyncio.to_thread(scraper.get, url)
        if not response:
            return PreviewListingPageResponse(
                url=url,
                links_found=0,
                sample_links=[],
                next_page_url=None,
                warnings=[f"Não foi possível aceder à URL: {url}"],
            )

        html = response.text
        links = parse_listing_links(html, base_url, full_selectors)
        next_page = parse_next_page(html, base_url, full_selectors)

        if not links:
            warnings.append(
                "Nenhum link de anúncio encontrado. "
                "Verifica o 'listing_link_selector' e o 'link_pattern'."
            )
        if not next_page and selectors.get("next_page_selector"):
            warnings.append(
                "Seletor de próxima página configurado mas não encontrado. "
                "Pode ser a última página, ou o seletor está errado."
            )

        return PreviewListingPageResponse(
            url=url,
            links_found=len(links),
            sample_links=links[:5],
            next_page_url=next_page,
            warnings=warnings,
        )

    except Exception as e:
        logger.error("Listing page preview failed for %s: %s", url, str(e))
        return PreviewListingPageResponse(
            url=url,
            links_found=0,
            sample_links=[],
            next_page_url=None,
            warnings=[f"Erro: {str(e)}"],
        )
    finally:
        scraper.close()