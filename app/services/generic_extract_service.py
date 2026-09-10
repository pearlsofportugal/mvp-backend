"""Generic single-URL extraction — for sites with no dedicated partner config.

Layered, free-first pipeline:
  1. Fetch (EthicalScraper → PlaywrightScraper fallback), robots.txt not enforced
     (the caller is a human pasting one link they are already viewing).
  2. Extract, merged in priority order:
       a. parse_listing_page() with empty selectors — already runs the JSON-LD /
          OpenGraph fallback, image parse, SEO scrape and feature keyword scan.
       b. suggest_selectors() heuristic engine → re-parse with the suggested
          selectors → fill gaps.
       c. regex heuristics on cleaned page text for still-missing price/area/
          typology/energy.
       d. image gallery heuristic when the image list looks weak.
       e. optional Gemini fallback (settings.generic_extract_llm_fallback) — only
          when title OR price is still missing after a–d.
  3. normalize_generic_payload() → canonical PropertySchema.

Nothing is written to the database here.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.config import settings
from app.core.logging import get_logger
from app.crawler.html_cache import html_cache
from app.crawler.selector_suggester import suggest_selectors
from app.schemas.property_schema import PropertySchema
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import normalize_generic_payload
from app.services.parser_service import (
    _extract_body_text_without_chrome,
    parse_listing_page,
)
from app.services.playwright_scraper import PlaywrightScraper

logger = get_logger(__name__)


class GenericExtractError(Exception):
    """Raised when a page cannot be fetched or is a bot-protection challenge page."""


# Suggester field name → parser selector key
_SUGGESTER_TO_PARSER_KEY = {
    "price": "price_selector",
    "title": "title_selector",
    "area": "area_selector",
    "land_area": "land_area_selector",
    "rooms": "bedrooms_selector",
    "bathrooms": "bathrooms_selector",
    "property_type": "property_type_selector",
    "typology": "typology_selector",
    "condition": "condition_selector",
    "business_type": "business_type_selector",
    "district": "district_selector",
    "county": "county_selector",
    "parish": "parish_selector",
    "images": "image_selector",
}
_MIN_SUGGESTER_SCORE = 0.55

_CHALLENGE_MARKERS = (
    "just a moment...",
    "cf-browser-verification",
    "challenge-platform",
    "_cf_chl_opt",
    "captcha-delivery.com",
    "datadome",
    "px-captcha",
    "attention required",
    "access denied",
    "enable javascript and cookies to continue",
)

_PRICE_RE = re.compile(r"(\d[\d.\s]{2,}\d)\s*(?:€|eur|euros?)", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]\b", re.IGNORECASE)
_TYPOLOGY_RE = re.compile(r"\b([TV]\d+(?:\+\d+)?)\b")
_ENERGY_RE = re.compile(
    r"(?:certificad|classe)[^.]{0,30}?\b([A-G][+-]?)\b", re.IGNORECASE
)
_GALLERY_SELECTORS = (
    "[class*='gallery'] img", "[class*='galeria'] img", "[class*='slider'] img",
    "[class*='carousel'] img", "[class*='foto'] img", "[class*='thumb'] img",
    "[id*='gallery'] img", "[id*='fotos'] img", "[id*='galeria'] img",
)


def _looks_like_challenge_page(html: str) -> bool:
    lowered = html[:4000].lower()
    if any(marker in lowered for marker in _CHALLENGE_MARKERS):
        return True
    # Very small page with no listing-ish content is almost always a block page.
    return len(html) < 1500


async def _fetch(url: str) -> str:
    """Fetch page HTML, EthicalScraper first, Playwright as JS fallback."""
    ethical = EthicalScraper(
        user_agent="RealEstateResearchBot/1.0 (+contact: scraper@pearlsofportugal.com)",
        min_delay=0.0,
        max_delay=0.5,
        respect_robots=False,
    )
    try:
        response = await asyncio.to_thread(ethical.get, url)
        html = response.text if response is not None else None
    except Exception as exc:  # noqa: BLE001 — surface as a clean error
        logger.warning("generic fetch (ethical) failed for %s: %s", url, exc)
        html = None
    finally:
        ethical.close()

    if html and not _looks_like_challenge_page(html):
        return html

    logger.info("generic fetch: falling back to Playwright for %s", url)
    pw = PlaywrightScraper(min_delay=0.0, max_delay=0.5, timeout=30, respect_robots=False)
    try:
        html = await pw.get_html(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("generic fetch (playwright) failed for %s: %s", url, exc)
        html = None
    finally:
        await pw.close()

    if not html:
        raise GenericExtractError("Could not fetch this page.")
    if _looks_like_challenge_page(html):
        raise GenericExtractError("This site is protected against automated access.")
    return html


def _mapped_suggested_selectors(suggest_result: dict) -> dict[str, str]:
    """Turn suggest_selectors() output into a parser-ready selectors dict."""
    selectors: dict[str, str] = {}
    for field, candidates in (suggest_result.get("candidates") or {}).items():
        parser_key = _SUGGESTER_TO_PARSER_KEY.get(field)
        if not parser_key or not candidates:
            continue
        best = candidates[0]
        if best.get("score", 0) >= _MIN_SUGGESTER_SCORE and best.get("selector"):
            selectors[parser_key] = best["selector"]
    return selectors


def _text_heuristics(soup: BeautifulSoup, raw: dict) -> None:
    """Fill still-missing scalar fields from a regex sweep of the cleaned body text."""
    text = _extract_body_text_without_chrome(soup)

    if not raw.get("price"):
        m = _PRICE_RE.search(text)
        if m:
            raw["price"] = m.group(0)
    if not raw.get("area"):
        m = _AREA_RE.search(text)
        if m:
            raw["area"] = m.group(0)
    if not raw.get("typology"):
        m = _TYPOLOGY_RE.search(raw.get("title") or "") or _TYPOLOGY_RE.search(text)
        if m:
            raw["typology"] = m.group(1).upper()
    if not raw.get("energy_certificate"):
        m = _ENERGY_RE.search(text)
        if m:
            raw["energy_certificate"] = m.group(1).upper()


def _image_heuristic(soup: BeautifulSoup, url: str, raw: dict) -> None:
    """Pull a gallery when the parser's image list is empty or too small."""
    existing = raw.get("images") or []
    if len(existing) >= 3:
        return
    base = urlparse(url)
    best: list[str] = []
    for selector in _GALLERY_SELECTORS:
        urls: list[str] = []
        try:
            elements = soup.select(selector)
        except Exception:  # noqa: BLE001 — bad selector, skip
            continue
        for img in elements:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src or src.startswith("data:"):
                continue
            if src.startswith("//"):
                src = f"{base.scheme}:{src}"
            elif src.startswith("/"):
                src = f"{base.scheme}://{base.netloc}{src}"
            urls.append(src)
        if len(urls) > len(best):
            best = urls
    if len(best) > len(existing):
        raw["images"] = best
        raw["alt_texts"] = [""] * len(best)


def _llm_fallback(soup: BeautifulSoup, url: str, raw: dict) -> None:
    """One Gemini Flash call to fill core fields — only when title/price still missing."""
    from app.adapters.gemini_adapter import gemini_adapter

    page_text = _extract_body_text_without_chrome(soup)[:12000]
    system_instruction = (
        "You extract structured data from a real estate listing page. "
        "Return ONLY a JSON object with these keys (use null when unknown): "
        "title, price (as written, with currency), property_type, typology, "
        "bedrooms, bathrooms, area (with unit), gross_area, land_area, "
        "district, county, parish, energy_certificate, condition, raw_description. "
        "Do not invent values that are not present in the text."
    )
    try:
        result = gemini_adapter.generate(
            system_instruction=system_instruction,
            prompt=f"URL: {url}\n\nPage text:\n{page_text}",
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001 — fallback must never break the pipeline
        logger.warning("generic LLM fallback failed for %s: %s", url, exc)
        return

    for key, value in (result or {}).items():
        if value in (None, "", []):
            continue
        if not raw.get(key):
            raw[key] = value


_PROVENANCE_FIELDS = (
    "title", "price", "property_type", "typology", "bedrooms", "bathrooms",
    "area", "gross_area", "land_area", "district", "county", "parish",
    "energy_certificate", "condition", "images", "raw_description",
)


async def extract_generic(url: str) -> tuple[PropertySchema, dict[str, str | None], dict]:
    """Extract a listing from an unconfigured site. Returns (schema, provenance, raw)."""
    html = await _fetch(url)
    await html_cache.set(url, html)  # prime cache so suggest_selectors() reuses it
    soup = BeautifulSoup(html, "lxml")

    # Layer a — structured data + built-in fallbacks
    raw = parse_listing_page(html, url, selectors={}, extraction_mode="direct")
    provenance = {f: ("structured_data" if raw.get(f) else None) for f in _PROVENANCE_FIELDS}

    # Layer b — selector suggester
    try:
        suggest_result = await suggest_selectors(url)
        suggested = _mapped_suggested_selectors(suggest_result)
        if suggested:
            raw_suggested = parse_listing_page(html, url, suggested, "direct")
            for key, value in raw_suggested.items():
                if value and not raw.get(key):
                    raw[key] = value
                    _bump_provenance(provenance, key, "suggester")
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggest_selectors failed for %s: %s", url, exc)

    # Layer c — regex heuristics
    before = {f: raw.get(f) for f in _PROVENANCE_FIELDS}
    _text_heuristics(soup, raw)
    for f in _PROVENANCE_FIELDS:
        if raw.get(f) and not before.get(f):
            _bump_provenance(provenance, f, "heuristic")

    # Layer d — image gallery heuristic
    if not raw.get("images"):
        _image_heuristic(soup, url, raw)
        if raw.get("images"):
            _bump_provenance(provenance, "images", "heuristic")

    # Layer e — optional LLM fallback
    if settings.generic_extract_llm_fallback and (not raw.get("title") or not raw.get("price")):
        before = {f: raw.get(f) for f in _PROVENANCE_FIELDS}
        _llm_fallback(soup, url, raw)
        for f in _PROVENANCE_FIELDS:
            if raw.get(f) and not before.get(f):
                _bump_provenance(provenance, f, "llm")

    host = (urlparse(url).hostname or "unknown").lower()
    source_partner = "generic_" + re.sub(r"[^a-z0-9]+", "_", host).strip("_")

    schema = normalize_generic_payload(raw, source_partner)
    return schema, provenance, raw


def _bump_provenance(provenance: dict[str, str | None], parser_key: str, layer: str) -> None:
    """Record which layer supplied a field (parser keys → provenance field names)."""
    key_map = {"useful_area": "area"}
    field = key_map.get(parser_key, parser_key)
    if field in provenance and provenance.get(field) is None:
        provenance[field] = layer
