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
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

from app.config import settings
from app.core.logging import get_logger
from app.crawler.html_cache import html_cache
from app.crawler.selector_suggester import _is_in_excluded_region, suggest_selectors
from app.schemas.property_schema import PropertySchema
from app.services.ego_extract_service import (
    ego_source_partner,
    extract_ego_platform,
    looks_like_ego_platform,
)
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import (
    PRICE_ON_REQUEST,
    normalize_ego_platform_payload,
    normalize_generic_payload,
    parse_price,
)
from app.services.parser_service import (
    _CHROME_TAGS,
    _RELATED_LISTINGS_RE,
    _extract_body_text_without_chrome,
    parse_listing_page,
    strip_repeated_price_cards,
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

# Digit groups must be real thousands groups ("1 234 567", "1.234.567") or a
# plain run of 4+ digits. The old `\d[\d.\s]{2,}\d` also matched per-digit
# spans that get_text(" ") joins with single spaces ("2 2 2 1 €", luximos.pt).
_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d{4,}(?:,\d{1,2})?)\s*(?:€|eur|euros?)",
    re.IGNORECASE,
)
_MAX_PLAUSIBLE_PRICE = 200_000_000
_PRICE_CONTEXT_RE = re.compile(r"pre[çc]o|price|valor|venda|for sale|asking|desde|from", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]\b", re.IGNORECASE)
_TYPOLOGY_RE = re.compile(r"\b([TV]\d+(?:\+\d+)?)\b")
# PT listing titles very often spell the typology out ("Apartamento 3
# Quartos") instead of using the T-code — a title-scoped, per-listing signal.
# Preferred over a body-text sweep for _TYPOLOGY_RE: on a "development" page
# advertising several units (e.g. "30 apartamentos T2 e T3"), the body text
# contains every typology in the development and a naive first-match grabs
# whichever is mentioned first, not necessarily this specific unit's.
_ROOMS_WORD_RE = re.compile(r"\b(\d+)[\s-]*(?:quartos?|bed(?:room)?s?)\b", re.IGNORECASE)
_PT_NUMBER_WORDS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6}
_ROOMS_PT_WORDS_RE = re.compile(r"\b(um|uma|dois|duas|tr[êe]s|quatro|cinco|seis)\s+quartos?\b", re.IGNORECASE)
_ENERGY_RE = re.compile(
    r"(?:certificad|classe)[^.]{0,30}?\b([A-G][+-]?)\b", re.IGNORECASE
)
# The generic path's only source for raw_description is JSON-LD/meta
# description (parse_listing_page's structured-data layer) — sites almost
# always cap that meta tag at ~155-160 chars for SEO snippets, so it's
# routinely a mid-sentence truncation of the real body copy, never the full
# "Sobre o imóvel" text a human sees. These are common containers for that
# fuller text; picks the longest match, ignoring anything in a
# related-listings widget (same reasoning as the typology fix above).
_DESCRIPTION_CONTAINER_SELECTORS = (
    "[class*='descricao']", "[id*='descricao']",
    "[class*='description']", "[id*='description']",
    "[class*='sobre']", "[itemprop='description']",
)
_MIN_DESCRIPTION_LENGTH = 200


def _description_looks_truncated(text: str) -> bool:
    text = text.strip()
    if len(text) < _MIN_DESCRIPTION_LENGTH:
        return True
    return text[-1] not in ".!?…\"”)”"


# Text blocks that are never a property description: contact/consent forms,
# legal footers, cookie notices. Seen winning a naive "longest paragraph
# group" on pinkrealestate.pt (footer links), carlomonteiro.pt (form
# disclaimer) and principal-algarve.com ("fill in the form below").
_NOT_A_DESCRIPTION_RE = re.compile(
    r"formul[aá]rio|privacidade|privacy|cookies?|termos|terms\s+and|dados\s+pessoais|"
    r"personal\s+data|gdpr|consent|newsletter|copyright|reservados|preencher",
    re.IGNORECASE,
)


# `var x =` (real JS) — a bare "var " substring also matches ordinary words
# ("louvar ", "elevar ") and wrongly rejected 24propertyportugal.com's good text.
_JS_VAR_RE = re.compile(r"\bvar\s+\w+\s*=")


def _looks_like_prose(text: str) -> bool:
    """Reject markup/script/legal debris masquerading as a description.

    Seen: an IE conditional comment ("[if lt IE 9]> <script src=...") on
    accer.pt and a Google Tag Manager snippet on algarve-property-agency.com
    were both picked up as the "description".
    """
    if len(text) < _MIN_DESCRIPTION_LENGTH:
        return False
    if any(tok in text for tok in ("<", "{", "function(", "src=", "gtag(")) or _JS_VAR_RE.search(text):
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters / len(text) >= 0.6 and text.count(" ") >= 25


def _best_paragraph_group(soup: BeautifulSoup) -> str:
    """Longest run of sibling <p> text outside chrome/forms/related-listings.

    Many CMSs put the listing description as several <p> under one container
    with no descriptive class/id, so selector-based lookup misses it.
    """
    clean = BeautifulSoup(str(soup), "lxml")
    for tag_name in (*_CHROME_TAGS, "form"):
        for el in clean.find_all(tag_name):
            el.decompose()
    for el in clean.find_all(id=_RELATED_LISTINGS_RE):
        el.decompose()
    for el in clean.find_all(class_=_RELATED_LISTINGS_RE):
        el.decompose()
    strip_repeated_price_cards(clean)
    best = ""
    for parent in clean.find_all(True):
        paragraphs = parent.find_all("p", recursive=False)
        if not paragraphs:
            continue
        text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        if len(text) > len(best) and not _NOT_A_DESCRIPTION_RE.search(text) and _looks_like_prose(text):
            best = text
    if len(best) < _MIN_DESCRIPTION_LENGTH:
        # No <p> run — some CMSs emit the description as bare text (with <br>)
        # directly inside a <div>. Take the element with the most direct text.
        for el in clean.find_all(True):
            # type() is NavigableString excludes Comment/CData/Doctype nodes.
            own = " ".join(
                t.strip() for t in el.find_all(string=True, recursive=False)
                if type(t) is NavigableString and t.strip()
            )
            if len(own) > len(best) and not _NOT_A_DESCRIPTION_RE.search(own) and _looks_like_prose(own):
                best = own
    return best if len(best) >= _MIN_DESCRIPTION_LENGTH else ""


def _normalize_typology(raw: dict) -> None:
    """Keep only a real T-code ("T2", "T3+1"); drop anything else.

    Selectors sometimes return a whole label ("Tipologia T2", ville.pt) or an
    unrelated token (a listing reference "RC2001-231", rc20.pt) as typology.
    """
    value = raw.get("typology")
    if not value:
        return
    m = _TYPOLOGY_RE.search(str(value).upper())
    if m:
        raw["typology"] = m.group(1)
    else:
        raw.pop("typology", None)


def _validate_price(raw: dict) -> None:
    """Drop a raw price that can't be a real price, so later layers refill it.

    Seen: '320000000000' straight from structured data (24propertyportugal),
    and page chrome text ("Tipo Propriedade Apartamento Herdade ...",
    principal-algarve.com) picked up by the selector layer. Because a raw
    price was *present*, the regex/LLM layers never ran to correct it.
    """
    value = raw.get("price")
    if not value:
        return
    amount, _currency = parse_price(str(value))
    if amount == PRICE_ON_REQUEST:
        return
    if amount is None or amount > _MAX_PLAUSIBLE_PRICE:
        raw.pop("price", None)


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
    _validate_price(raw)
    _normalize_typology(raw)
    text = _extract_body_text_without_chrome(soup)

    if not raw.get("price"):
        # Conservative: a bare "first € amount" is how a neighbouring listing's
        # price (a grid card whose markup varied between fetches, accer.pt) or
        # a fee got ingested as the price. Accept only an unambiguous amount
        # (the single distinct one in the cleaned text) or one with a price
        # keyword right before it; otherwise leave it empty so the LLM
        # fallback, which can read context, decides.
        found = []
        for m in _PRICE_RE.finditer(text):
            if _plausible_price_text(m.group(1)):
                digits = re.sub(r"\D", "", m.group(1).split(",")[0])
                context = text[max(0, m.start() - 60):m.start()]
                found.append((digits, m.group(0), bool(_PRICE_CONTEXT_RE.search(context))))
        chosen = None
        if len({d for d, _, _ in found}) == 1:
            chosen = found[0][1]
        else:
            chosen = next((g for _, g, has_ctx in found if has_ctx), None)
        if chosen:
            raw["price"] = chosen
    if not raw.get("area"):
        m = _AREA_RE.search(text)
        if m:
            raw["area"] = m.group(0)
    if not raw.get("typology"):
        title = raw.get("title") or ""
        m = _TYPOLOGY_RE.search(title)
        if m:
            raw["typology"] = m.group(1).upper()
        else:
            rooms_m = _ROOMS_WORD_RE.search(title)
            if rooms_m:
                raw["typology"] = f"T{rooms_m.group(1)}"
            else:
                # Description next (it describes *this* listing), then the
                # body — but the body only when it names exactly one
                # typology. A whole-page "first T\d" picks up search-filter
                # buttons ("T0 T1 T2 ...", accer.pt → T0) and neighbouring
                # cards; an ambiguous page is left to the LLM fallback.
                desc = raw.get("raw_description") or ""
                m = _TYPOLOGY_RE.search(desc)
                words_m = _ROOMS_PT_WORDS_RE.search(desc)
                if m:
                    raw["typology"] = m.group(1).upper()
                elif words_m:
                    raw["typology"] = f"T{_PT_NUMBER_WORDS[words_m.group(1).lower().replace('ê', 'e')]}"
                else:
                    codes = {c.upper() for c in _TYPOLOGY_RE.findall(text)}
                    if len(codes) == 1:
                        raw["typology"] = codes.pop()
    if not raw.get("energy_certificate"):
        m = _ENERGY_RE.search(text)
        if m:
            raw["energy_certificate"] = m.group(1).upper()

    current_description = raw.get("raw_description") or ""
    if _description_looks_truncated(current_description):
        best = current_description
        for selector in _DESCRIPTION_CONTAINER_SELECTORS:
            for el in soup.select(selector):
                if _is_in_excluded_region(el):
                    continue
                candidate = el.get_text(" ", strip=True)
                if len(candidate) > len(best) and _looks_like_prose(candidate):
                    best = candidate
        if len(best) < _MIN_DESCRIPTION_LENGTH:
            # No descriptively-named container — fall back to the longest
            # run of sibling paragraphs outside forms/footers/related widgets.
            best = max(best, _best_paragraph_group(soup), key=len)
        if len(best) > len(current_description):
            raw["raw_description"] = best


def _prefer_og_image(soup: BeautifulSoup, url: str, raw: dict) -> None:
    """Move the page's declared main image (og:image) to the front of the list.

    The raw list is in DOM order, so it often starts with site chrome (logos,
    language flags, agent portraits, tracking pixels). og:image is what the
    site itself says represents *this* page. Only trusted when it is already
    among the collected images (or the list is empty): many sites reuse one
    generic banner as og:image on every page, which we must not promote.
    """
    tag = soup.select_one("meta[property='og:image'], meta[name='og:image'], meta[name='twitter:image']")
    src = urljoin(url, (tag.get("content") or "").strip()) if tag else ""
    if not src:
        return
    images = list(raw.get("images") or [])
    alts = list(raw.get("alt_texts") or [])
    alts += [""] * (len(images) - len(alts))
    if src in images:
        i = images.index(src)
        images.insert(0, images.pop(i))
        alts.insert(0, alts.pop(i))
    elif not images:
        images, alts = [src], [""]
    else:
        return
    raw["images"], raw["alt_texts"] = images, alts


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
        "district, county, parish, energy_certificate, condition, "
        "construction_year (four-digit year the building was built, as an integer), "
        "raw_description. "
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

    # Gemini occasionally wraps the object in an array ([{...}]) — seen on
    # engelvoelkers.com, where `.items()` on the list crashed the whole ingest.
    if isinstance(result, list):
        result = next((item for item in result if isinstance(item, dict)), None)
    if not isinstance(result, dict):
        logger.warning("generic LLM fallback returned non-object for %s", url)
        return

    for key, value in result.items():
        if value in (None, "", []):
            continue
        if not raw.get(key):
            raw[key] = value
    _normalize_typology(raw)


_PROVENANCE_FIELDS = (
    "title", "price", "property_type", "typology", "bedrooms", "bathrooms",
    "area", "gross_area", "land_area", "district", "county", "parish",
    "energy_certificate", "condition", "construction_year", "images", "raw_description",
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
    _prefer_og_image(soup, url, raw)

    # Layer e — optional LLM fallback. Fires on missing title/price (the
    # original trigger), or when the whole address block came back empty —
    # observed live on accer.pt: title/price were both filled (the price was
    # even wrong, picked up from price_per_m2), so the old title-or-price
    # check never ran, yet district/county/parish were all blank and the LLM
    # recovered all three correctly from the same page text.
    address_blank = not raw.get("district") and not raw.get("county") and not raw.get("parish")
    if settings.generic_extract_llm_fallback and (not raw.get("title") or not raw.get("price") or address_blank):
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
