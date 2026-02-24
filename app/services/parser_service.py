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
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger
from app.database import async_session_factory

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Configuration Cache
# ═══════════════════════════════════════════════════════════

_FIELD_MAP_CACHE: Dict[str, str] = {}
_FEATURE_MAP_CACHE: Dict[str, str] = {}
_CACHE_TIMESTAMP: Optional[datetime] = None
_CACHE_TTL_SECONDS = 300  # 5 minutes

# Default fallback mappings (used if DB is unavailable)
_DEFAULT_FIELD_MAP = {
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
        from app.models.field_mapping import FieldMapping

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


def _get_field_map() -> Dict[str, str]:
    if _FIELD_MAP_CACHE:
        return _FIELD_MAP_CACHE
    return _DEFAULT_FIELD_MAP


def _get_feature_map() -> Dict[str, str]:
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
    selectors: Dict[str, Any],
) -> List[str]:
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
    selectors: Dict[str, Any],
) -> Optional[str]:
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
    selectors: Dict[str, Any],
    extraction_mode: str = "direct",
) -> Dict[str, Any]:
    """Parse a single listing detail page into a raw dict."""
    soup = BeautifulSoup(html, "lxml")
    data: Dict[str, Any] = {"url": url}

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

def _parse_section_based(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse using section-based extraction (name/value pairs)."""
    data: Dict[str, Any] = {}

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


def _safe_select_one(soup: BeautifulSoup, selector: str) -> Optional[Tag]:
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

def _extract_name_value_pairs(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
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


def _extract_divisions(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
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


def _extract_area_pairs(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
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


def _extract_characteristics(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
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


# ═══════════════════════════════════════════════════════════
# Direct Selector Parsing
# ═══════════════════════════════════════════════════════════

def _parse_direct_selectors(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse using direct CSS selectors for each field."""
    data: Dict[str, Any] = {}

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
    }

    for selector_key, field in simple_fields.items():
        selector = selectors.get(selector_key)
        if not selector:
            continue
        el = soup.select_one(selector)
        if el:
            data[field] = el.get_text(strip=True)

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
        feature_map = _get_feature_map()
        for el in soup.select(features_selector):
            text = el.get_text(strip=True).lower()
            for keyword, mapped_field in feature_map.items():
                if keyword in text:
                    data[mapped_field] = "Yes"
                    break

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

    return data


# ═══════════════════════════════════════════════════════════
# Common Extractions
# ═══════════════════════════════════════════════════════════

def _parse_images(soup: BeautifulSoup, selectors: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """Extract images from the listing page."""
    data: Dict[str, Any] = {"images": [], "alt_texts": []}

    image_selector = selectors.get("image_selector", "img")
    image_filter = selectors.get("image_filter")

    for img in soup.select(image_selector):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue

        absolute_url = urljoin(base_url, src)

        if image_filter and not re.search(image_filter, absolute_url):
            continue

        data["images"].append(absolute_url)
        data["alt_texts"].append(img.get("alt", ""))

    return data


def _parse_seo(soup: BeautifulSoup) -> Dict[str, Any]:
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


def _extract_via_text_patterns(soup: BeautifulSoup, patterns: Dict[str, str]) -> Dict[str, Any]:
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
                value = match.group(1).strip()
                if value:
                    data[field] = value
        except Exception as e:
            logger.warning("Error applying pattern for %s: %s", field, str(e))

    return data


def parse_listing_card(
    card_html: str,
    selectors: Dict[str, Any],
    base_url: str,
) -> Dict[str, Any]:
    """Parse a listing card from the search results page (minimal data)."""
    soup = BeautifulSoup(card_html, "lxml")
    data: Dict[str, Any] = {}

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