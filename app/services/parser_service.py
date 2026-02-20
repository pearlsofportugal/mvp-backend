"""Parser service — HTML parsing for property listings with DB-configurable field mappings.

Supports two extraction modes:
1. Section-based: extracts from sections with .name/.value pairs (e.g., Pearls of Portugal)
2. Direct selector: CSS selectors for each field directly (configurable per site)

CONFIGURATION:
  - Field name translations loaded from 'field_mappings' table
  - Feature detection keywords loaded from 'field_mappings' table (type='feature')
  - Configurations are cached with TTL for performance
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
    "price": "price",
    "preço": "price",
    "valor": "price",
    "typology": "typology",
    "tipologia": "typology",
    "tipo": "typology",
    "bedrooms": "bedrooms",
    "quartos": "bedrooms",
    "assoalhadas": "bedrooms",
    "bathrooms": "bathrooms",
    "casas de banho": "bathrooms",
    "wc": "bathrooms",
    "floor": "floor",
    "andar": "floor",
    "piso": "floor",
    "energy certificate": "energy_certificate",
    "certificado energético": "energy_certificate",
    "classe energética": "energy_certificate",
    "construction year": "construction_year",
    "ano de construção": "construction_year",
    "ano": "construction_year",
    "property type": "property_type",
    "tipo de imóvel": "property_type",
    "district": "district",
    "distrito": "district",
    "county": "county",
    "concelho": "county",
    "parish": "parish",
    "freguesia": "parish",
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

    # Check cache validity
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
            logger.debug("Loaded %d field mappings and %d feature mappings from DB", 
                        len(field_map), len(feature_map))

    except Exception as e:
        logger.warning("Could not load field mappings from DB: %s. Using defaults.", str(e))
        _FIELD_MAP_CACHE = _DEFAULT_FIELD_MAP.copy()
        _FEATURE_MAP_CACHE = _DEFAULT_FEATURE_MAP.copy()


def _get_field_map() -> Dict[str, str]:
    """Get cached field map synchronously."""
    if _FIELD_MAP_CACHE:
        return _FIELD_MAP_CACHE
    return _DEFAULT_FIELD_MAP


def _get_feature_map() -> Dict[str, str]:
    """Get cached feature map synchronously."""
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
    """Extract listing page URLs from a listing/search results page.

    Args:
        html: Raw HTML content of the listing page.
        base_url: Base URL for resolving relative links.
        selectors: Site selectors dict with 'listing_link_selector' and optional 'listing_link_pattern'.

    Returns:
        List of absolute listing URLs.
    """
    soup = BeautifulSoup(html, "lxml")
    link_selector = selectors.get("listing_link_selector", "a")
    link_pattern = selectors.get("listing_link_pattern")

    links = []
    for a_tag in soup.select(link_selector):
        href = a_tag.get("href")
        if not href:
            continue
        absolute_url = urljoin(base_url, href)

        # Filter by pattern if configured
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
    """Extract the next page URL from pagination.

    Returns None if there is no next page.
    """
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
    """Parse a single listing detail page into a raw dict.

    Args:
        html: Raw HTML of the listing detail page.
        url: URL of the listing (for reference).
        selectors: Site selectors configuration.
        extraction_mode: "section" or "direct".

    Returns:
        Dict with raw parsed values (strings, to be normalized by the mapper).
    """
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
# Section-Based Parsing (name/value pairs extraction)
# ═══════════════════════════════════════════════════════════

def _parse_section_based(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse using section-based extraction (name/value pairs).

    Extracts data from sections with .name and .value elements (e.g., #details, #areas).
    Also supports regex-based text pattern extraction via 'text_patterns' config.
    Configurable via selectors in site_configs table.
    """
    data: Dict[str, Any] = {}

    # Title
    title_selector = selectors.get("title_selector")
    if title_selector:
        title_el = soup.select_one(title_selector)
        if title_el:
            data["title"] = title_el.get_text(strip=True)

    # Location (address)
    location_selector = selectors.get("location_selector")
    if location_selector:
        loc_el = soup.select_one(location_selector)
        if loc_el:
            data["location"] = loc_el.get_text(strip=True)

    # Condition (e.g., "Used", "New")
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
                if text and len(text) > 50:  # Só se tiver conteúdo substancial
                    data["raw_description"] = text
                    break

    # Regex-based text pattern extraction (for semi-structured text)
    text_patterns = selectors.get("text_patterns", {})
    if text_patterns:
        data.update(_extract_via_text_patterns(soup, text_patterns))

    # Details section (Price, Typology, etc.) - fallback if patterns didn't extract
    details_section = selectors.get("details_section")
    if details_section and details_section != "body":
        section = soup.select_one(details_section)
        if section:
            extracted = _extract_name_value_pairs(section, selectors)
            logger.debug("Extracted %d fields from details section", len(extracted))
            # Merge without overwriting existing values
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

    # Characteristics / features section
    chars_section = selectors.get("characteristics_section")
    if chars_section:
        section = soup.select_one(chars_section)
        if section:
            data.update(_extract_characteristics(section, selectors))

    # Nearby / Proximities section
    nearby_section = selectors.get("nearby_section")
    if nearby_section:
        section = soup.select_one(nearby_section)
        if section:
            nearby_items = []
            item_selector = selectors.get("nearby_item_selector", ".name")
            for item in section.select(item_selector):
                text = item.get_text(strip=True)
                if text:
                    nearby_items.append(text)
            if nearby_items:
                data["nearby"] = nearby_items

    # ── Contact / Advertiser info ──
    advertiser_selector = selectors.get("advertiser_selector")
    if advertiser_selector:
        el = soup.select_one(advertiser_selector)
        if el:
            data["advertiser"] = el.get_text(strip=True)

    advertiser_phone = selectors.get("advertiser_phone_selector")
    if advertiser_phone:
        el = soup.select_one(advertiser_phone)
        if el:
            data["advertiser_phone"] = el.get_text(strip=True)

    advertiser_email = selectors.get("advertiser_email_selector")
    if advertiser_email:
        el = soup.select_one(advertiser_email)
        if el:
            data["advertiser_email"] = el.get_text(strip=True)

    advertiser_logo = selectors.get("advertiser_logo_selector")
    if advertiser_logo:
        el = soup.select_one(advertiser_logo)
        if el:
            src = el.get("src") or el.get("data-src")
            if src:
                data["advertiser_logo"] = src

    # ── Features extraction (bulk selector) ──
    features_selector = selectors.get("features_selector")
    if features_selector:
        feature_map = _get_feature_map()
        for el in soup.select(features_selector):
            text = el.get_text(strip=True).lower()
            for keyword, mapped_field in feature_map.items():
                if keyword in text:
                    data[mapped_field] = "Yes"
                    break

    # ── Individual feature selectors ──
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
        if selector:
            el = soup.select_one(selector)
            if el:
                data[field] = "Yes"

    # ── Publication date ──
    date_selector = selectors.get("publication_date_selector")
    if date_selector:
        el = soup.select_one(date_selector)
        if el:
            data["publication_date"] = el.get_text(strip=True)

    # ── Price per m² ──
    price_m2_selector = selectors.get("price_per_m2_selector")
    if price_m2_selector:
        el = soup.select_one(price_m2_selector)
        if el:
            data["price_per_m2"] = el.get_text(strip=True)

    # ── Business type ──
    business_type_selector = selectors.get("business_type_selector")
    if business_type_selector:
        el = soup.select_one(business_type_selector)
        if el:
            data["business_type"] = el.get_text(strip=True)

    return data


def _extract_via_text_patterns(soup: BeautifulSoup, patterns: Dict[str, str]) -> Dict[str, Any]:
    """Extract data using regex patterns applied to the full page text.
    
    Args:
        soup: BeautifulSoup object of the page
        patterns: Dict mapping field names to regex patterns
        
    Returns:
        Dict with extracted field values
    """
    data = {}
    
    # Get full text and HTML for pattern matching
    full_text = soup.get_text(separator=" ", strip=True)
    full_html = str(soup)
    
    for field, pattern in patterns.items():
        try:
            # Try matching against text first, then HTML
            match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if not match:
                match = re.search(pattern, full_html, re.IGNORECASE | re.DOTALL)
            
            if match:
                value = match.group(1).strip()
                if value:
                    data[field] = value
                    logger.debug("Extracted %s='%s' via regex pattern", field, value[:50])
        except Exception as e:
            logger.warning("Error applying pattern for %s: %s", field, str(e))
    
    return data


def _extract_name_value_pairs(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Extract name/value pairs from a details section using DB-configured mappings."""
    data = {}
    name_selector = selectors.get("detail_name_selector", ".name")
    value_selector = selectors.get("detail_value_selector", ".value")

    # Get field map from cache
    field_map = _get_field_map()

    items = section.select(selectors.get("detail_item_selector", ".detail"))
    for item in items:
        name_el = item.select_one(name_selector)
        value_el = item.select_one(value_selector)
        if name_el and value_el:
            name = name_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)
            
            # Try to match against field map
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
    """Extract boolean characteristics/amenities from a features section using DB-configured mappings."""
    data = {}
    items = section.select(selectors.get("char_item_selector", ".name"))

    # Get feature map from cache
    feature_map = _get_feature_map()

    for item in items:
        text = item.get_text(strip=True).lower()
        for keyword, field in feature_map.items():
            if keyword in text:
                data[field] = "Yes"
                break

    return data


# ═══════════════════════════════════════════════════════════
# Direct Selector Parsing (configurable per site)
# ═══════════════════════════════════════════════════════════

def _parse_direct_selectors(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse using direct CSS selectors for each field."""
    data = {}

    # Map of selector key -> output field
    field_selectors = {
        "title_selector": "title",
        "price_selector": "price",
        "description_selector": "raw_description",
        "typology_selector": "typology",
        "useful_area_selector": "useful_area",
        "gross_area_selector": "gross_area",
        "area_selector": "useful_area",
        "bedrooms_selector": "bedrooms",
        "bathrooms_selector": "bathrooms",
        "floor_selector": "floor",
        "construction_year_selector": "construction_year",
        "district_selector": "district",
        "county_selector": "county",
        "parish_selector": "parish",
        "energy_certificate_selector": "energy_certificate",
        "condition_selector": "condition",
        "location_selector": "location",
        "property_type_selector": "property_type",
        "property_id_selector": "property_id",
        "business_type_selector": "business_type",
        "price_per_m2_selector": "price_per_m2",
        "publication_date_selector": "publication_date",
        # Contact / Advertiser
        "advertiser_selector": "advertiser",
        "advertiser_phone_selector": "advertiser_phone",
        "advertiser_email_selector": "advertiser_email",
        # SEO (fallback if not auto-detected)
        "meta_description_selector": "meta_description",
        "page_title_selector": "page_title",
    }

    for selector_key, field_name in field_selectors.items():
        selector = selectors.get(selector_key)
        if selector:
            el = soup.select_one(selector)
            if el:
                data[field_name] = el.get_text(strip=True)

    # Features (look for checkmarks/icons near feature text)
    feature_selectors = {
        "garage_selector": "garage",
        "elevator_selector": "elevator",
        "balcony_selector": "balcony",
        "air_conditioning_selector": "air_conditioning",
        "ac_selector": "air_conditioning",
        "pool_selector": "swimming_pool",
        "garden_selector": "garden",
        "features_selector": None,  # handled separately
    }

    for feature_key, field in feature_selectors.items():
        selector = selectors.get(feature_key)
        if not selector:
            continue
        if feature_key == "features_selector":
            # Bulk features extraction
            feature_map = _get_feature_map()
            for el in soup.select(selector):
                text = el.get_text(strip=True).lower()
                for keyword, mapped_field in feature_map.items():
                    if keyword in text:
                        data[mapped_field] = "Yes"
                        break
        else:
            el = soup.select_one(selector)
            if el and field:
                data[field] = "Yes"

    # Advertiser logo
    logo_selector = selectors.get("advertiser_logo_selector")
    if logo_selector:
        el = soup.select_one(logo_selector)
        if el:
            src = el.get("src") or el.get("data-src")
            if src:
                data["advertiser_logo"] = urljoin("https:", src) if src.startswith("//") else src

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

        # Filter by pattern
        if image_filter and not re.search(image_filter, absolute_url):
            continue

        data["images"].append(absolute_url)
        alt = img.get("alt", "")
        data["alt_texts"].append(alt)

    return data


def _parse_seo(soup: BeautifulSoup) -> Dict[str, Any]:
    """Extract SEO-relevant elements from the page."""
    data = {}

    # Page title
    title_tag = soup.find("title")
    if title_tag:
        data["page_title"] = title_tag.get_text(strip=True)

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        data["meta_description"] = meta_desc["content"]

    # Headers
    headers = []
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(strip=True)
            if text:
                headers.append({"level": f"h{level}", "text": text})
    if headers:
        data["headers"] = headers

    return data


def parse_listing_card(
    card_html: str,
    selectors: Dict[str, Any],
    base_url: str,
) -> Dict[str, Any]:
    """Parse a listing card from the search results page (minimal data)."""
    soup = BeautifulSoup(card_html, "lxml") if isinstance(card_html, str) else card_html
    data = {}

    card_selector_map = {
        "card_title_selector": "title",
        "card_price_selector": "price",
        "card_location_selector": "location",
        "card_area_selector": "useful_area",
        "card_typology_selector": "typology",
    }

    for selector_key, field in card_selector_map.items():
        selector = selectors.get(selector_key)
        if selector:
            el = soup.select_one(selector)
            if el:
                data[field] = el.get_text(strip=True)

    # Link
    link_selector = selectors.get("listing_link_selector", "a")
    link_el = soup.select_one(link_selector)
    if link_el and link_el.get("href"):
        data["url"] = urljoin(base_url, link_el["href"])

    return data


# ═══════════════════════════════════════════════════════════
# Async initialization helper
# ═══════════════════════════════════════════════════════════

async def init_parser_cache() -> None:
    """Initialize the parser cache by loading field mappings from DB.
    
    Call this at application startup or before first scrape.
    """
    await _load_field_mappings()
