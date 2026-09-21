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
from app.services.ego_extract_service import (
    ego_source_partner,
    extract_ego_platform,
    looks_like_ego_platform,
)
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import (
    normalize_ego_platform_payload,
    normalize_generic_payload,
)
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
# Bare label words that a mis-scored selector sometimes yields as a "value".
_LABEL_VALUES = frozenset({
    "preço", "preco", "price", "valor", "área", "area", "área útil", "area util",
    "quartos", "quarto", "casas de banho", "wc", "wcs", "tipologia", "typology",
    "distrito", "concelho", "freguesia", "estado", "condition", "n/d", "n/a", "-",
})
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


_CHROME_TITLE_MARKERS = (
    "detalhes de imóvel", "detalhes de imovel", "ficha do imóvel", "ficha do imovel",
    "property details", "::", " | ",
)


def _title_looks_like_chrome(title: str | None) -> bool:
    if not title:
        return True
    low = title.lower()
    return any(m in low for m in _CHROME_TITLE_MARKERS)


async def _fetch_static(url: str) -> str | None:
    ethical = EthicalScraper(
        user_agent="RealEstateResearchBot/1.0 (+contact: scraper@pearlsofportugal.com)",
        min_delay=0.0, max_delay=0.5, respect_robots=False,
    )
    try:
        response = await asyncio.to_thread(ethical.get, url)
        return response.text if response is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("generic fetch (ethical) failed for %s: %s", url, exc)
        return None
    finally:
        ethical.close()


async def _fetch_rendered(url: str) -> str | None:
    pw = PlaywrightScraper(min_delay=0.0, max_delay=0.5, timeout=30, respect_robots=False)
    try:
        return await pw.get_html(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("generic fetch (playwright) failed for %s: %s", url, exc)
        return None
    finally:
        await pw.close()


async def _fetch(url: str) -> str:
    """Fetch page HTML: static first, Playwright when static is blocked or empty."""
    html = await _fetch_static(url)
    if html and not _looks_like_challenge_page(html):
        return html

    logger.info("generic fetch: falling back to Playwright for %s", url)
    html = await _fetch_rendered(url)
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


def _strip_label_values(raw: dict) -> None:
    """Null out fields whose value is just a bare label word (a mis-scored selector)."""
    for key in ("price", "area", "useful_area", "gross_area", "typology", "condition",
                "district", "county", "parish", "property_type", "bedrooms", "bathrooms"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip().lower() in _LABEL_VALUES:
            raw.pop(key, None)


def _plausible_price_text(candidate: str) -> bool:
    """Reject regex price matches that are actually years or too small to be a price."""
    digits = re.sub(r"[^\d]", "", candidate)
    if not digits:
        return False
    value = int(digits)
    if 1900 <= value <= 2100 and len(digits) == 4:  # a year, not a price
        return False
    return value >= 150  # cheapest plausible monthly rent


def _text_heuristics(soup: BeautifulSoup, raw: dict) -> None:
    """Fill still-missing scalar fields from a regex sweep of the cleaned body text."""
    _strip_label_values(raw)
    text = _extract_body_text_without_chrome(soup)

    if not raw.get("price"):
        for m in _PRICE_RE.finditer(text):
            if _plausible_price_text(m.group(1)):
                raw["price"] = m.group(0)
                break
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

    # eGO Real Estate platform — a single adapter covers every eGO-generated
    # agency site. The listing data is JS-injected, so render before parsing.
    if looks_like_ego_platform(html):
        ego_result = await _extract_via_ego(url, html)
        if ego_result is not None:
            return ego_result

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

    # Drop mis-scored selector values that are just a label word, and clear their
    # (now wrong) provenance so the completeness report stays honest.
    _strip_label_values(raw)
    for f in _PROVENANCE_FIELDS:
        if provenance.get(f) and not (raw.get(f) or raw.get("useful_area" if f == "area" else "")):
            provenance[f] = None

    # If the static HTML gave us essentially nothing (chrome-only title, no price),
    # the page is probably JS-rendered — retry once with a real browser.
    if _title_looks_like_chrome(raw.get("title")) and not raw.get("price"):
        rendered = await _fetch_rendered(url)
        if rendered and not _looks_like_challenge_page(rendered):
            html, soup = rendered, BeautifulSoup(rendered, "lxml")
            await html_cache.set(url, html)
            r2 = parse_listing_page(html, url, selectors={}, extraction_mode="direct")
            for key, value in r2.items():
                overwrite = not raw.get(key)
                if key == "title" and value and _title_looks_like_chrome(raw.get("title")):
                    overwrite = not _title_looks_like_chrome(value)
                if value and overwrite:
                    raw[key] = value
                    _bump_provenance(provenance, key, "structured_data")
            _strip_label_values(raw)

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


async def _extract_via_ego(
    url: str, static_html: str
) -> tuple[PropertySchema, dict[str, str | None], dict] | None:
    """eGO platform path: render (data is JS-injected), parse, normalize."""
    # eGO server-renders some blocks but lazy-loads others (gallery, agent
    # sidebar, parts of the specs) — always render so nothing is missed.
    rendered = await _fetch_rendered(url)
    html = rendered if (rendered and not _looks_like_challenge_page(rendered)) else static_html

    raw = extract_ego_platform(html, url)
    if not raw.get("title") and not raw.get("price"):
        logger.info("eGO detected but nothing extracted for %s — falling back to generic", url)
        return None

    await html_cache.set(url, html)
    schema = normalize_ego_platform_payload(raw, ego_source_partner(url))
    provenance = {
        f: "ego_platform"
        for f in _PROVENANCE_FIELDS
        if raw.get(f) or (f == "area" and raw.get("useful_area"))
    }
    return schema, provenance, raw


def _bump_provenance(provenance: dict[str, str | None], parser_key: str, layer: str) -> None:
    """Record which layer supplied a field (parser keys → provenance field names)."""
    key_map = {"useful_area": "area"}
    field = key_map.get(parser_key, parser_key)
    if field in provenance and provenance.get(field) is None:
        provenance[field] = layer
