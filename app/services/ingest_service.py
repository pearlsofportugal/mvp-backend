"""Ingest service — orchestrates single-URL extraction for the /api/v1/ingest endpoint.

Two paths:
  - partner_config: the host matches an active SiteConfig → precise CSS-selector
    extraction + the registered partner normalizer (same pipeline as the scheduled
    scraper, minus persistence and minus test-scrape's response truncation).
  - generic: no matching SiteConfig → generic_extract_service layered pipeline.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.repositories.site_config_repository import SiteConfigRepository
from app.schemas.ingest_schema import IngestCompleteness, IngestResponse
from app.schemas.property_schema import PropertySchema
from app.services.ethics_service import EthicalScraper
from app.services.generic_extract_service import GenericExtractError, extract_generic
from app.services.mapper_service import normalize_partner_payload
from app.services.parser_service import parse_listing_page
from app.services.playwright_scraper import PlaywrightScraper

logger = get_logger(__name__)

# Fields the Client Area cares about — drives the completeness report.
_REPORT_FIELDS = (
    "title", "price", "property_type", "typology", "bedrooms", "bathrooms",
    "area_useful_m2", "area_gross_m2", "area_land_m2", "district", "county",
    "parish", "energy_certificate", "condition", "construction_year", "media",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


async def _match_site_config(db: AsyncSession, url: str):
    target = _host(url)
    if not target:
        return None
    for site in await SiteConfigRepository.get_all(db, include_inactive=False):
        if _host(site.base_url or "") == target:
            return site
    return None


async def _fetch_for_partner(site, url: str) -> str | None:
    if site.use_js_render:
        selectors = site.selectors or {}
        pw = PlaywrightScraper(
            min_delay=0.0, max_delay=0.5, timeout=30,
            content_ready_selector=selectors.get("title_selector") or selectors.get("price_selector"),
        )
        try:
            return await pw.get_html(url)
        finally:
            await pw.close()
    ethical = EthicalScraper(user_agent="MVPScraper/1.0 (+ingest)", min_delay=0.0, max_delay=0.5)
    try:
        response = await asyncio.to_thread(ethical.get, url)
        return response.text if response is not None else None
    finally:
        ethical.close()


async def _extract_via_partner(site, url: str) -> tuple[PropertySchema, dict, dict]:
    html = await _fetch_for_partner(site, url)
    if not html:
        raise GenericExtractError("Could not fetch this page.")
    raw = parse_listing_page(html, url, site.selectors or {}, site.extraction_mode)
    schema = normalize_partner_payload(raw, site.key)
    provenance = {f: "partner_config" for f in _REPORT_FIELDS if _field_value(schema, f)}
    return schema, provenance, raw


def _field_value(schema: PropertySchema, field: str):
    if field == "price":
        return schema.price.amount if schema.price else None
    if field == "media":
        return schema.media or None
    if field in ("district", "county", "parish"):
        return {"district": schema.address.region, "county": schema.address.city,
                "parish": schema.address.area}[field]
    return getattr(schema, field, None)


def _completeness(schema: PropertySchema) -> IngestCompleteness:
    populated, missing = [], []
    for field in _REPORT_FIELDS:
        (populated if _field_value(schema, field) else missing).append(field)
    return IngestCompleteness(populated=populated, missing=missing)


async def ingest_listing(db: AsyncSession, url: str) -> IngestResponse:
    """Extract a single listing from any URL. Never writes to the database."""
    url = str(url)
    site = await _match_site_config(db, url)

    try:
        if site is not None:
            schema, provenance, raw = await _extract_via_partner(site, url)
            source = "partner_config"
        else:
            schema, provenance, raw = await extract_generic(url)
            source = "ego_platform" if (schema.source_partner or "").startswith("ego_") else "generic"
    except GenericExtractError as exc:
        return IngestResponse(url=url, success=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — one bad page must not 500 the endpoint
        logger.exception("ingest failed for %s", url)
        return IngestResponse(url=url, success=False, error=f"Extraction failed: {exc}")

    if not schema.title and schema.price.amount is None:
        return IngestResponse(
            url=url, success=False, source=source,
            error="Could not extract this listing — the page may have changed, "
                  "requires login, or exposes no usable data.",
        )

    return IngestResponse(
        url=url,
        success=True,
        source=source,
        property=schema,
        raw=_json_safe_raw(raw),
        completeness=_completeness(schema),
        field_provenance=provenance,
    )


def _json_safe_raw(raw: dict) -> dict:
    """Drop unserializable values and keep the raw dict JSON-friendly (no truncation)."""
    safe: dict = {}
    for key, value in (raw or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [v for v in value if isinstance(v, (str, int, float, bool))]
        elif isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items() if isinstance(v, (str, int, float, bool))}
        else:
            safe[key] = str(value)
    return safe
