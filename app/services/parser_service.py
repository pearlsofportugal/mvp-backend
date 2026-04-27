"""Parser service — HTML parsing for property listings with DB-configurable field mappings.

Supports two extraction modes:
1. Section-based: extracts from sections with .name/.value pairs (e.g., Pearls of Portugal)
2. Direct selector: CSS selectors for each field directly (configurable per site)

CONFIGURATION:
  - Field name translations loaded from 'field_mappings' table
  - Feature detection keywords loaded from 'field_mappings' table (type='feature')
  - Configurations are cached with TTL for performance

FIXES (v2):
  - Added divisions_section support (extracts bedrooms/bathrooms from icon+name+value layout)
  - Fixed energy certificate extraction from <img> src when no text value exists
  - Added "business type" / "business state" to _DEFAULT_FIELD_MAP
  - _extract_name_value_pairs now falls back to img.icon alt/src when value_el is empty
    - Direct mode now supports attribute/img fallback values and Habinédita icon block extraction
"""
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger
from app.database import async_session_factory

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Configuration Cache
# ═══════════════════════════════════════════════════════════

_FIELD_MAP_CACHE: dict[str, str] = {}
_FEATURE_MAP_CACHE: dict[str, str] = {}
_CACHE_TIMESTAMP: datetime | None = None
_CACHE_TTL_SECONDS = 300  # 5 minutes
_DEBUG_HTML_PREVIEW_CHARS = 700

# Default fallback mappings (used if DB is unavailable)
_DEFAULT_FIELD_MAP = {
    # Summary field mappings
    "objectivo": "business_type",
    "objetivo": "business_type",
    # Price
    "price": "price",
    "preço": "price",
    "valor": "price",

    # Business type — FIX: added "business type" and "business state"
    "business type": "business_type",
    "business state": "business_state",
    "tipo de negócio": "business_type",
    "tipo negócio": "business_type",

    # Typology
    "typology": "typology",
    "tipologia": "typology",
    "tipo": "typology",

    # Bedrooms
    "bedrooms": "bedrooms",
    "quartos": "bedrooms",
    "assoalhadas": "bedrooms",

    # Bathrooms
    "bathrooms": "bathrooms",
    "casas de banho": "bathrooms",
    "wc": "bathrooms",
    "living rooms": "living_rooms",
    "salas": "living_rooms",

    # Floor
    "floor": "floor",
    "andar": "floor",
    "piso": "floor",

    # Energy certificate
    "energy certificate": "energy_certificate",
    "certificado energético": "energy_certificate",
    "classe energética": "energy_certificate",
    "energy class": "energy_certificate",

    # Construction year
    "construction year": "construction_year",
    "ano de construção": "construction_year",

    # Property type
    "property type": "property_type",
    "tipo de imóvel": "property_type",

    # Location
    "district": "district",
    "distrito": "district",
    "county": "county",
    "concelho": "county",
    "parish": "parish",
    "freguesia": "parish",

    # Reference / ID
    "reference": "property_id",
    "referência": "property_id",
    "ref": "property_id",
}

_DEFAULT_FEATURE_MAP = {
    "garage": "garage",
    "garagem": "garage",
    "parking": "garage",
    "estacionamento": "garage",
    "box": "garage",
    "elevator": "elevator",
    "elevador": "elevator",
    "lift": "elevator",
    "balcony": "balcony",
    "varanda": "balcony",
    "terraço": "balcony",
    "terrace": "balcony",
    "marquise": "balcony",
    "air conditioning": "air_conditioning",
    "ar condicionado": "air_conditioning",
    "a/c": "air_conditioning",
    "climatização": "air_conditioning",
    "pool": "swimming_pool",
    "piscina": "swimming_pool",
    "swimming": "swimming_pool",
}

_SUMMARY_FIELD_MAP = {
    "objectivo": "business_type",
    "objetivo": "business_type",
    "tipo de negócio": "business_type",
    "estado": "condition",
    "tipo": "property_type",
    "tipo de imóvel": "property_type",
    "tipologia": "typology",
    "tipologia do imóvel": "typology",
    "distrito": "district",
    "concelho": "county",
    "freguesia": "parish",
    "zona": "zone",
}


def _get_summary_field_map() -> dict[str, str]:
    """Return the summary field map, preferring DB-loaded mappings when available."""
    db_map = _get_field_map()
    if db_map is _DEFAULT_FIELD_MAP:
        # DB map not yet loaded or fallback — use the static summary map as-is
        return _SUMMARY_FIELD_MAP
    # Merge: DB map wins; static summary map fills in keys absent from DB
    merged = {**_SUMMARY_FIELD_MAP, **db_map}
    return merged


async def _load_field_mappings() -> None:
    """Load field mappings from DB with caching."""
    global _FIELD_MAP_CACHE, _FEATURE_MAP_CACHE, _CACHE_TIMESTAMP

    now = datetime.now(timezone.utc)

    if (
        _CACHE_TIMESTAMP
        and _FIELD_MAP_CACHE
        and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return

    try:
        from sqlalchemy import select
        from app.models.field_mapping_model import FieldMapping

        async with async_session_factory() as db:
            result = await db.execute(
                select(FieldMapping).where(FieldMapping.is_active.is_(True))
            )
            mappings = result.scalars().all()

            field_map = {}
            feature_map = {}

            for m in mappings:
                if m.mapping_type == "field":
                    field_map[m.source_name.lower()] = m.target_field
                elif m.mapping_type == "feature":
                    feature_map[m.source_name.lower()] = m.target_field

            if field_map:
                _FIELD_MAP_CACHE = field_map
            if feature_map:
                _FEATURE_MAP_CACHE = feature_map

            _CACHE_TIMESTAMP = now
            logger.debug(
                "Loaded %d field mappings and %d feature mappings from DB",
                len(field_map),
                len(feature_map),
            )

    except Exception as e:
        logger.warning("Could not load field mappings from DB: %s. Using defaults.", str(e))
        _FIELD_MAP_CACHE = _DEFAULT_FIELD_MAP.copy()
        _FEATURE_MAP_CACHE = _DEFAULT_FEATURE_MAP.copy()


def _get_field_map() -> dict[str, str]:
    if _FIELD_MAP_CACHE:
        return _FIELD_MAP_CACHE
    return _DEFAULT_FIELD_MAP


def _get_feature_map() -> dict[str, str]:
    if _FEATURE_MAP_CACHE:
        return _FEATURE_MAP_CACHE
    return _DEFAULT_FEATURE_MAP


def invalidate_parser_cache():
    """Clear the parser configuration cache (call after config updates)."""
    global _FIELD_MAP_CACHE, _FEATURE_MAP_CACHE, _CACHE_TIMESTAMP
    _FIELD_MAP_CACHE = {}
    _FEATURE_MAP_CACHE = {}
    _CACHE_TIMESTAMP = None
    logger.info("Parser configuration cache invalidated")


# ═══════════════════════════════════════════════════════════
# Public Parsing Functions
# ═══════════════════════════════════════════════════════════

def parse_listing_links(
    html: str,
    base_url: str,
    selectors: dict[str, Any],
) -> list[str]:
    """Extract listing page URLs from a listing/search results page."""
    soup = BeautifulSoup(html, "lxml")
    link_selector = selectors.get("listing_link_selector", "a")
    link_pattern = selectors.get("listing_link_pattern")

    links = []
    for a_tag in soup.select(link_selector):
        href = a_tag.get("href")
        if not href:
            continue
        absolute_url = urljoin(base_url, href)

        if link_pattern and not re.search(link_pattern, absolute_url):
            continue

        if absolute_url not in links:
            links.append(absolute_url)

    logger.info("Found %d listing links on page", len(links))
    return links


def parse_next_page(
    html: str,
    base_url: str,
    selectors: dict[str, Any],
) -> str | None:
    """Extract the next page URL from pagination."""
    soup = BeautifulSoup(html, "lxml")
    next_selector = selectors.get("next_page_selector")

    if not next_selector:
        return None

    next_link = soup.select_one(next_selector)
    if next_link and next_link.get("href"):
        return urljoin(base_url, next_link["href"])

    return None


def parse_listing_page(
    html: str,
    url: str,
    selectors: dict[str, Any],
    extraction_mode: str = "direct",
) -> dict[str, Any]:
    """Parse a single listing detail page into a raw dict."""
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, Any] = {"url": url}

    if extraction_mode == "section":
        data.update(_parse_section_based(soup, selectors))
    else:
        data.update(_parse_direct_selectors(soup, selectors))

    # Common extractions (both modes)
    data.update(_parse_images(soup, selectors, url))
    data.update(_parse_seo(soup))
    return data


# ═══════════════════════════════════════════════════════════
# Section-Based Parsing
# ═══════════════════════════════════════════════════════════

def _parse_section_based(soup: BeautifulSoup, selectors: dict[str, Any]) -> dict[str, Any]:
    """Parse using section-based extraction (name/value pairs)."""
    data: dict[str, Any] = {}
    # Title
    title_selector = selectors.get("title_selector")
    if title_selector:
        title_el = soup.select_one(title_selector)
        if title_el:
            data["title"] = title_el.get_text(strip=True)

    # Location
    location_selector = selectors.get("location_selector")
    if location_selector:
        loc_el = soup.select_one(location_selector)
        if loc_el:
            data["location"] = loc_el.get_text(strip=True)

    # Condition
    condition_selector = selectors.get("condition_selector")
    if condition_selector:
        cond_el = soup.select_one(condition_selector)
        if cond_el:
            data["condition"] = cond_el.get_text(strip=True)

    # Description
    desc_selector = selectors.get("description_selector")
    if desc_selector:
        for selector in desc_selector.split(","):
            desc_el = soup.select_one(selector.strip())
            if desc_el:
                text = desc_el.get_text(strip=True)
                if text and len(text) > 50:
                    data["raw_description"] = text
                    break

    # Text pattern extraction
    text_patterns = selectors.get("text_patterns", {})
    if text_patterns:
        data.update(_extract_via_text_patterns(soup, text_patterns))

    summary_section = selectors.get("summary_section")
    if summary_section:
        section = _safe_select_one(soup, summary_section)
        if section:
            extracted = _extract_summary_pairs(section, selectors)
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    # Details section (price, type, district, energy cert…)
    details_section = selectors.get("details_section")
    if details_section and details_section != "body":
        section = soup.select_one(details_section)
        if section:
            extracted = _extract_name_value_pairs(section, selectors)
            logger.debug("Extracted %d fields from details section", len(extracted))
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    # Areas section
    areas_section = selectors.get("areas_section")
    if areas_section:
        section = soup.select_one(areas_section)
        if section:
            extracted = _extract_area_pairs(section, selectors)
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    # ── FIX: Divisions section (bedrooms / bathrooms / living rooms) ──────────
    # Some sites (e.g. Pearls of Portugal) put bedrooms/bathrooms in a separate
    # section#divisions with icon + .name + .value layout, not in section#details.
    divisions_section = selectors.get("divisions_section")
    if divisions_section:
        section = soup.select_one(divisions_section)
        if section:
            extracted = _extract_divisions(section, selectors)
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    # Characteristics / features section
    chars_section = selectors.get("characteristics_section")
    if chars_section:
        # soupsieve doesn't reliably support :last-of-type with ID selectors.
        # Try the selector directly; if it fails, fall back to selecting all
        # matching elements and taking the last one.
        section = _safe_select_one(soup, chars_section)
        if section:
            data.update(_extract_characteristics(section, selectors))

    # Nearby / Proximities section
    nearby_section = selectors.get("nearby_section")
    if nearby_section:
        section = _safe_select_one(soup, nearby_section)
        if section:
            nearby_items = []
            item_selector = selectors.get("nearby_item_selector", ".name")
            for item in section.select(item_selector):
                text = item.get_text(strip=True)
                if text:
                    nearby_items.append(text)
            if nearby_items:
                data["nearby"] = nearby_items

    return data


def _extract_summary_pairs(section: Tag, selectors: dict[str, Any]) -> dict[str, Any]:
    """Extract labeled summary pairs from list-based blocks."""
    data: dict[str, Any] = {}
    items = section.select(selectors.get("summary_item_selector", "li"))
    label_selector = selectors.get("summary_label_selector", "b, .name, .icon_label")
    value_selector = selectors.get("summary_value_selector", ".value, .lbl_valor")
    summary_map = _get_summary_field_map()

    for item in items:
        label_el = item.select_one(label_selector)
        if not label_el:
            continue

        label = label_el.get_text(" ", strip=True).rstrip(":").lower()
        field = summary_map.get(label)
        if not field:
            continue

        value_el = item.select_one(value_selector)
        if value_el:
            value = value_el.get_text(" ", strip=True)
        else:
            value = _extract_inline_value(item, label_el)

        if not value:
            continue

        data[field] = value

    return data


def _extract_inline_value(item: Tag, label_el: Tag) -> str:
    """Extract the value that follows a label node inside the same container."""
    fragments: list[str] = []

    for sibling in label_el.next_siblings:
        if isinstance(sibling, Tag):
            text = sibling.get_text(" ", strip=True)
        else:
            text = str(sibling).strip()

        if text:
            fragments.append(text)

    if fragments:
        return " ".join(fragments).strip()

    full_text = item.get_text(" ", strip=True)
    label_text = label_el.get_text(" ", strip=True)
    if full_text.startswith(label_text):
        return full_text[len(label_text):].strip(" :\t\n\r")

    return ""


def _safe_select_one(soup: BeautifulSoup, selector: str) -> Tag | None:
    """Select one element, with a fallback for pseudo-selectors that soupsieve
    may not support reliably (e.g. :last-of-type combined with ID selectors).

    If the selector raises an error or returns nothing, falls back to selecting
    all matches and returning the last one.
    """
    try:
        el = soup.select_one(selector)
        if el:
            return el
        # If select_one returned None, try selecting all and taking last
        # (handles :last-of-type / :first-of-type fallback)
        all_els = soup.select(selector)
        return all_els[-1] if all_els else None
    except Exception:
        # Strip pseudo-class and retry with the base selector
        base_selector = re.sub(r':[a-z-]+(\([^)]*\))?', '', selector).strip()
        if base_selector and base_selector != selector:
            try:
                all_els = soup.select(base_selector)
                return all_els[-1] if all_els else None
            except Exception:
                pass
    return None


# ═══════════════════════════════════════════════════════════
# Section Helpers
# ═══════════════════════════════════════════════════════════

def _extract_name_value_pairs(section: Tag, selectors: dict[str, Any]) -> dict[str, Any]:
    """Extract name/value pairs from a details section.

    FIX: When the value element is missing or empty (e.g. energy certificate
    rendered as an <img> instead of text), falls back to reading the img src
    and extracting the energy class letter via regex.
    """
    data = {}
    name_selector = selectors.get("detail_name_selector", ".name")
    value_selector = selectors.get("detail_value_selector", ".value")
    field_map = _get_field_map()

    items = section.select(selectors.get("detail_item_selector", ".detail"))
    for item in items:
        name_el = item.select_one(name_selector)
        if not name_el:
            continue

        name = name_el.get_text(strip=True).lower()

        # ── Try normal value element first ──
        value_el = item.select_one(value_selector)
        value = value_el.get_text(strip=True) if value_el else ""

        # ── FIX: Fallback for energy certificate rendered as <img> ──────────
        # e.g. <div class="detail">
        #        <div class="name">Energy Certificate</div>
        #        <img class="icon" src="/img/icons/energy/energy-d.png">
        #      </div>
        if not value:
            img = item.select_one("img")
            if img:
                # Try alt text first (e.g. alt="Energy Certificate D")
                alt = (img.get("alt") or "").strip()
                # Extract trailing letter A-G from alt
                alt_match = re.search(r'\b([A-G])\b', alt, re.IGNORECASE)
                if alt_match:
                    value = alt_match.group(1).upper()
                else:
                    # Fall back to parsing the src filename
                    # e.g. "energy-d.png" or "energy_class_b.svg"
                    src = img.get("src") or img.get("data-src") or ""
                    src_match = re.search(r'energy[-_]([a-g])', src, re.IGNORECASE)
                    if src_match:
                        value = src_match.group(1).upper()

        if not value:
            continue

        # Map field name to canonical key
        for key, field in field_map.items():
            if key in name:
                data[field] = value
                break

    return data


def _extract_divisions(section: Tag, selectors: dict[str, Any]) -> dict[str, Any]:
    """Extract bedrooms/bathrooms/living rooms from a divisions section.

    Pearls of Portugal layout:
      <div class="division">
        <img class="icon" src="...bedrooms.png">
        <div class="name">Bedrooms</div>
        <div class="value">5</div>
      </div>

    Uses the same field_map as _extract_name_value_pairs so it works
    for any language (Bedrooms / Quartos / Bathrooms / Casas de Banho).
    """
    data = {}
    item_selector = selectors.get("division_item_selector", "div.division")
    name_selector = selectors.get("division_name_selector", "div.name")
    value_selector = selectors.get("division_value_selector", "div.value")
    field_map = _get_field_map()

    for item in section.select(item_selector):
        name_el = item.select_one(name_selector)
        value_el = item.select_one(value_selector)
        if not name_el or not value_el:
            continue

        name = name_el.get_text(strip=True).lower()
        value = value_el.get_text(strip=True)
        if not value:
            continue

        for key, field in field_map.items():
            if key in name:
                data[field] = value
                break

    return data


def _extract_area_pairs(section: Tag, selectors: dict[str, Any]) -> dict[str, Any]:
    """Extract area measurements from an areas section."""
    data = {}
    items = section.select(selectors.get("area_item_selector", ".area"))
    name_selector = selectors.get("area_name_selector", ".name")
    value_selector = selectors.get("area_value_selector", ".value")

    for item in items:
        name_el = item.select_one(name_selector)
        value_el = item.select_one(value_selector)
        if name_el and value_el:
            name = name_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)

            if "useful" in name or "útil" in name:
                data["useful_area"] = value
            elif "gross" in name or "bruta" in name:
                data["gross_area"] = value
            elif "land" in name or "terreno" in name:
                data["land_area"] = value

    return data


def _extract_characteristics(section: Tag, selectors: dict[str, Any]) -> dict[str, Any]:
    """Extract boolean characteristics/amenities from a features section."""
    data = {}
    items = section.select(selectors.get("char_item_selector", ".name"))
    feature_map = _get_feature_map()

    for item in items:
        text = item.get_text(strip=True).lower()
        for keyword, field in feature_map.items():
            if keyword in text:
                data[field] = "Yes"
                break

    return data


def _debug_html_snippet(node: Tag | BeautifulSoup, max_chars: int = _DEBUG_HTML_PREVIEW_CHARS) -> str:
    """Return a compact HTML snippet for print-based debugging."""
    raw_html = node.decode() if hasattr(node, "decode") else str(node)
    compact_html = re.sub(r"\s+", " ", raw_html).strip()
    if len(compact_html) <= max_chars:
        return compact_html
    return f"{compact_html[:max_chars]}..."


def _selector_debug_enabled(selectors: dict[str, Any]) -> bool:
    """Enable print-based selector debugging only when explicitly requested."""
    return bool(
        selectors.get("_debug_selectors")
        or selectors.get("__debug_selectors__")
        or selectors.get("debug_selectors")
    )


def _print_selector_debug(
    soup: BeautifulSoup,
    enabled: bool,
    field: str,
    selector_key: str,
    selector: str | None,
    reason: str,
    matched_element: Tag | None = None,
) -> None:
    """Print detailed selector debug information."""
    if not enabled:
        return
    print("[selector-debug] field:", field, flush=True)
    print("[selector-debug] selector_key:", selector_key, flush=True)
    print("[selector-debug] selector:", selector, flush=True)
    print("[selector-debug] reason:", reason, flush=True)
    print(
        "[selector-debug] html:",
        _debug_html_snippet(matched_element or (soup.body or soup)),
        flush=True,
    )


def _extract_energy_certificate_value(raw_value: str) -> str | None:
    """Normalize energy certificate text to the expected rating token."""
    match = re.search(r"\b([A-G])\b", raw_value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    compact = raw_value.strip().upper()
    if compact in {"A+", "A", "B", "B-", "C", "D", "E", "F", "G"}:
        return compact

    return None


def _extract_element_value(el: Tag, field: str | None = None) -> str:
    """Extract a meaningful value from text, attributes, or nested images."""
    text = el.get_text(" ", strip=True)
    if text:
        if field == "energy_certificate":
            normalized = _extract_energy_certificate_value(text)
            if normalized:
                return normalized
        return text

    for attr in ("content", "value", "alt", "title", "aria-label", "data-value"):
        value = el.get(attr)
        if value and str(value).strip():
            normalized_value = str(value).strip()
            if field == "energy_certificate":
                normalized = _extract_energy_certificate_value(normalized_value)
                return normalized or normalized_value
            return normalized_value

    img = el if el.name == "img" else el.select_one("img")
    if img:
        for attr in ("alt", "title", "src", "data-src"):
            value = img.get(attr)
            if not value or not str(value).strip():
                continue

            normalized_value = str(value).strip()
            if field == "energy_certificate":
                normalized = _extract_energy_certificate_value(normalized_value)
                if normalized:
                    return normalized

                src_match = re.search(r"energy[-_]?([a-g])", normalized_value, re.IGNORECASE)
                if src_match:
                    return src_match.group(1).upper()

            return normalized_value

    # EgoRealEstate: energy class encoded as CSS class on <i> tag
    # e.g. <i class="energyClass APlus"> → "A+",  <i class="energyClass C"> → "C"
    if field == "energy_certificate":
        target = el if el.name == "i" else el.select_one("i[class*='energyClass']")
        if target:
            for cls in (target.get("class") or []):
                if cls.lower() == "energyclass":
                    continue
                if cls.lower() == "aplus":
                    return "A+"
                if cls.lower() in ("bminus", "b-"):
                    return "B-"
                if cls.upper() in {"A", "B", "C", "D", "E", "F", "G"}:
                    return cls.upper()

    return ""


def _assign_feature_matches(text: str, target: dict[str, Any]) -> None:
    """Assign every matching feature keyword instead of stopping at the first match."""
    normalized_text = text.lower()
    for keyword, mapped_field in _get_feature_map().items():
        if keyword in normalized_text:
            target[mapped_field] = "Yes"


def _first_selector_value(
    soup: BeautifulSoup,
    selectors: list[str],
    field: str | None = None,
) -> str | None:
    """Return the first non-empty value found for a list of CSS selectors."""
    for selector in selectors:
        try:
            el = soup.select_one(selector)
        except Exception:
            continue

        if not el:
            continue

        value = _extract_element_value(el, field=field)
        if value:
            return value

    return None


def _is_habinedita_like_page(soup: BeautifulSoup) -> bool:
    """Detect the Habinédita detail layout based on stable container ids."""
    return bool(
        soup.select_one("[id*='modulodadosicones']")
        or soup.select_one("[id*='div_imovel_descricao']")
    )


def _extract_habinedita_fallbacks(soup: BeautifulSoup, current_data: dict[str, Any]) -> dict[str, Any]:
    """Extract structured values from Habinédita's icon block when selectors miss them."""
    if not _is_habinedita_like_page(soup):
        return {}

    extracted: dict[str, Any] = {}
    icon_selectors = {
        "bedrooms": [
            "[id*='lbl_valor_quarto']",
            "[id*='lbl_valor_quartos']",
        ],
        "bathrooms": [
            "[id*='lbl_valor_wcs']",
            "[id*='lbl_valor_wc']",
        ],
        "gross_area": [
            "[id*='lbl_valor_area_bruta']",
        ],
        "useful_area": [
            "[id*='lbl_valor_area_util']",
            "[id*='lbl_valor_area_utile']",
            "[id$='lbl_valor_area']",  # exact suffix — avoids matching _area_bruta / _area_terreno
        ],
        "garage": [
            "[id*='lbl_valor_garagens']",
            "[id*='lbl_valor_garagem']",
        ],
        "land_area": [
            "[id*='lbl_valor_area_terreno']",
        ],
        "energy_certificate": [
            "[id*='div_certificacao'] img",
            "[id*='div_certificacao']",
            "[id*='lbl_icon_certificacao'] img",  # habinédita naming variant
            "[id*='lbl_icon_certificacao']",
        ],
    }

    for field, candidate_selectors in icon_selectors.items():
        if current_data.get(field):
            continue

        value = _first_selector_value(soup, candidate_selectors, field=field)
        if value:
            extracted[field] = value

    description_value = current_data.get("raw_description") or _first_selector_value(
        soup,
        ["[id*='div_imovel_descricao']", ".descricao", ".property-description"],
    )
    if description_value:
        _assign_feature_matches(description_value, extracted)
        if not extracted.get("energy_certificate"):
            normalized_energy = _extract_energy_certificate_value(description_value)
            if normalized_energy:
                extracted["energy_certificate"] = normalized_energy

    # Second pass: scan full visible page text for features that the description
    # div may have missed (e.g. nested elements, JS-rendered fragments, or the
    # description text containing feature info in non-div children).
    _FEATURE_KEYS = frozenset(_get_feature_map().values())
    missing_features = {k for k in _FEATURE_KEYS if not extracted.get(k) and not current_data.get(k)}
    if missing_features:
        full_page_text = soup.get_text(separator=" ", strip=True)
        partial: dict[str, Any] = {}
        _assign_feature_matches(full_page_text, partial)
        for key, value in partial.items():
            if key in missing_features:
                extracted[key] = value

    return extracted


# ═══════════════════════════════════════════════════════════
# Direct Selector Parsing
# ═══════════════════════════════════════════════════════════

def _parse_direct_selectors(soup: BeautifulSoup, selectors: dict[str, Any]) -> dict[str, Any]:
    """Parse using direct CSS selectors for each field."""
    data: dict[str, Any] = {}
    debug_enabled = _selector_debug_enabled(selectors)
    simple_fields = {
        "title_selector": "title",
        "location_selector": "location",
        "condition_selector": "condition",
        "property_type_selector": "property_type",
        "typology_selector": "typology",
        "bedrooms_selector": "bedrooms",
        "bathrooms_selector": "bathrooms",
        "floor_selector": "floor",
        "construction_year_selector": "construction_year",
        "energy_certificate_selector": "energy_certificate",
        "district_selector": "district",
        "county_selector": "county",
        "parish_selector": "parish",
        "price_selector": "price",
        "business_type_selector": "business_type",
        "price_per_m2_selector": "price_per_m2",
        "publication_date_selector": "publication_date",
        # Area fields — must be explicit here; they are not handled by any section extractor
        "area_selector": "area",
        "gross_area_selector": "gross_area",
        "land_area_selector": "land_area",
    }

    for selector_key, field in simple_fields.items():
        selector = selectors.get(selector_key)
        if not selector:
            _print_selector_debug(soup, debug_enabled, field, selector_key, None, "selector missing from site config")
            continue

        try:
            el = soup.select_one(selector)
        except Exception as exc:
            _print_selector_debug(soup, debug_enabled, field, selector_key, selector, f"invalid selector: {exc}")
            continue

        if not el:
            _print_selector_debug(soup, debug_enabled, field, selector_key, selector, "no HTML match for selector")
            continue

        value = _extract_element_value(el, field=field)
        if not value:
            _print_selector_debug(
                soup,
                debug_enabled,
                field,
                selector_key,
                selector,
                "selector matched element but extracted text is empty",
                matched_element=el,
            )
            continue

        if debug_enabled:
            print("[selector-debug] matched field:", field, flush=True)
            print("[selector-debug] selector_key:", selector_key, flush=True)
            print("[selector-debug] selector:", selector, flush=True)
            print("[selector-debug] value:", value, flush=True)
        data[field] = value

    if debug_enabled:
        print("[selector-debug] final simple field data:", data, flush=True)

    # Description (longer, needs length check)
    desc_selector = selectors.get("description_selector")
    if desc_selector:
        for selector in desc_selector.split(","):
            desc_el = soup.select_one(selector.strip())
            if desc_el:
                text = desc_el.get_text(strip=True)
                if text and len(text) > 50:
                    data["raw_description"] = text
                    break

    # Property ID (may be in attribute, not text)
    prop_id_selector = selectors.get("property_id_selector")
    if prop_id_selector:
        el = soup.select_one(prop_id_selector)
        if el:
            # Try text first, then common attributes
            text = el.get_text(strip=True)
            if text:
                data["property_id"] = text
            else:
                for attr in ("reference", "data-reference", "data-id", "content"):
                    val = el.get(attr)
                    if val:
                        data["property_id"] = val
                        break

    # Advertiser
    adv_selector = selectors.get("advertiser_selector")
    if adv_selector:
        el = soup.select_one(adv_selector)
        if el:
            data["advertiser"] = el.get_text(strip=True)

    # Features — bulk via feature map
    features_selector = selectors.get("features_selector")
    if features_selector:
        for el in soup.select(features_selector):
            text = el.get_text(strip=True).lower()
            _assign_feature_matches(text, data)

    # Individual feature selectors
    individual_features = {
        "garage_selector": "garage",
        "elevator_selector": "elevator",
        "balcony_selector": "balcony",
        "air_conditioning_selector": "air_conditioning",
        "pool_selector": "swimming_pool",
        "garden_selector": "garden",
    }
    for selector_key, field in individual_features.items():
        selector = selectors.get(selector_key)
        if selector and soup.select_one(selector):
            data[field] = "Yes"

    # Text patterns
    text_patterns = selectors.get("text_patterns", {})
    if text_patterns:
        data.update(_extract_via_text_patterns(soup, text_patterns))

    details_section = selectors.get("details_section")
    if details_section and details_section != "body":
        section = _safe_select_one(soup, details_section)
        if section:
            for key, value in _extract_name_value_pairs(section, selectors).items():
                if key not in data:
                    data[key] = value

    areas_section = selectors.get("areas_section")
    if areas_section:
        section = _safe_select_one(soup, areas_section)
        if section:
            for key, value in _extract_area_pairs(section, selectors).items():
                if key not in data:
                    data[key] = value

    divisions_section = selectors.get("divisions_section")
    if divisions_section:
        section = _safe_select_one(soup, divisions_section)
        if section:
            for key, value in _extract_divisions(section, selectors).items():
                if key not in data:
                    data[key] = value

    chars_section = selectors.get("characteristics_section")
    if chars_section:
        section = _safe_select_one(soup, chars_section)
        if section:
            data.update(_extract_characteristics(section, selectors))

    summary_section = selectors.get("summary_section")
    if summary_section:
        section = _safe_select_one(soup, summary_section)
        if section:
            data.update(_extract_summary_pairs(section, selectors))

    for field, value in _extract_habinedita_fallbacks(soup, data).items():
        if not data.get(field):
            data[field] = value

    # Universal feature fallback — runs regardless of site, catches any feature
    # keywords that direct selectors / habinedita fallback may have missed.
    feature_map = _get_feature_map()
    missing_feature_fields = {v for v in feature_map.values() if not data.get(v)}
    if missing_feature_fields:
        full_text = soup.get_text(separator=" ", strip=True).lower()
        for keyword, field in feature_map.items():
            if field in missing_feature_fields and keyword in full_text:
                data[field] = "Yes"
                missing_feature_fields.discard(field)

    return data


# ═══════════════════════════════════════════════════════════
# Common Extractions
# ═══════════════════════════════════════════════════════════

def _parse_images(soup: BeautifulSoup, selectors: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Extract images from the listing page."""
    data: dict[str, Any] = {"images": [], "alt_texts": []}

    image_selector = selectors.get("image_selector", "img")
    image_filter = selectors.get("image_filter")
    image_exclude_filter = selectors.get("image_exclude_filter")

    for img in soup.select(image_selector):
        # Skip data: URIs (lazy-load placeholders) to find the real CDN URL
        src = next(
            (
                v for v in (
                    img.get("src"),
                    img.get("data-src"),
                    img.get("data-lazy-src"),
                    img.get("data-imgthumb"),
                )
                if v and not v.startswith("data:")
            ),
            None,
        )
        if not src:
            continue

        absolute_url = urljoin(base_url, src)

        if image_filter and not re.search(image_filter, absolute_url):
            continue
        if image_exclude_filter and re.search(image_exclude_filter, absolute_url):
            continue

        data["images"].append(absolute_url)
        data["alt_texts"].append(img.get("alt", ""))

    return data


def _parse_seo(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract SEO-relevant elements from the page."""
    data = {}

    title_tag = soup.find("title")
    if title_tag:
        data["page_title"] = title_tag.get_text(strip=True)

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        data["meta_description"] = meta_desc["content"]

    headers = []
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(strip=True)
            if text:
                headers.append({"level": f"h{level}", "text": text})
    if headers:
        data["headers"] = headers

    return data


def _extract_via_text_patterns(soup: BeautifulSoup, patterns: dict[str, str]) -> dict[str, Any]:
    """Extract data using regex patterns applied to the full page text."""
    data = {}
    full_text = soup.get_text(separator=" ", strip=True)
    full_html = str(soup)

    for field, pattern in patterns.items():
        try:
            match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if not match:
                match = re.search(pattern, full_html, re.IGNORECASE | re.DOTALL)
            if match:
                raw = match.group(1)
                value = raw.strip() if raw is not None else ""
                if value:
                    data[field] = value
        except Exception as e:
            logger.warning("Error applying pattern for %s: %s", field, str(e))

    return data


def parse_listing_card(
    card_html: str,
    selectors: dict[str, Any],
    base_url: str,
) -> dict[str, Any]:
    """Parse a listing card from the search results page (minimal data)."""
    soup = BeautifulSoup(card_html, "lxml")
    data: dict[str, Any] = {}

    for field, selector_key in [
        ("title", "card_title_selector"),
        ("price", "card_price_selector"),
        ("location", "card_location_selector"),
    ]:
        selector = selectors.get(selector_key)
        if selector:
            el = soup.select_one(selector)
            if el:
                data[field] = el.get_text(strip=True)

    return data