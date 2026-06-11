"""Mapper service — normalizes raw parsed data into the canonical schema and DB models.

Handles:
- Price parsing: "250 000 €" → (250000.0, "EUR")
- Area parsing: "120 m²" → 120.0
- Boolean mapping: "Yes"/None → True/False
- Typology → bedrooms extraction: "T3" → 3
- Date parsing: various formats → datetime
- Image list → MediaAsset records
- Price-per-m2 calculation: price / gross_area

CONFIGURATION:
  - Currency symbol mappings loaded from 'character_mappings' table (category='currency')
  - Configurations are cached with TTL for performance

Expandable per partner — dispatcher pattern.
"""
import asyncio
import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.database import async_session_factory
from app.schemas.property_schema import (
    Address,
    ListingFlags,
    MediaAsset,
    Money,
    PropertySchema,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Configuration Cache
# ═══════════════════════════════════════════════════════════
_TYPOLOGY_PATTERN = re.compile(r"[TtVv](\d+)")
_EGO_REF_PATTERN = re.compile(r"^Ref\.\s*")
_CLEAN_DESC_PREFIX_PATTERN = re.compile(r"^(descriç[aã]o|description)\s*[:\-]?\s*", re.IGNORECASE)
_CLEAN_DESC_PUNCT_SPACES = re.compile(r"\s+([,.;:!?])")
_CLEAN_DESC_MISSING_SPACES = re.compile(r"([.!?;:])(\S)")
_MULTIPLE_SPACES_PATTERN = re.compile(r"\s+")
_URL_LOCATION_PATTERN = re.compile(r"/Imovel/[^/]+/[^/]+/([^/?#]+)/([^/?#]+)/([^/?#]+)/\d+")


_CURRENCY_MAP_CACHE: dict[str, str] = {}
_CACHE_TIMESTAMP: datetime | None = None
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_LOCK = asyncio.Lock()

# Default fallback currency mappings
_DEFAULT_CURRENCY_MAP = {
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "$": "USD",
    "usd": "USD",
    "£": "GBP",
    "gbp": "GBP",
    "R$": "BRL",
    "brl": "BRL",
    "¥": "JPY",
    "jpy": "JPY",
}

_LISTING_STRING_LIMITS = {
    "partner_id": 255,
    "source_partner": 50,
    "source_url": 2048,
    "title": 500,
    "listing_type": 20,
    "property_type": 50,
    "typology": 10,
    "floor": 20,
    "price_currency": 3,
    "district": 100,
    "county": 100,
    "parish": 100,
    "full_address": 500,
    "energy_certificate": 20,
    "advertiser": 255,
    "contacts": 500,
    "page_title": 500,
}


async def _load_currency_map() -> dict[str, str]:
    """Load currency symbol mappings from DB with caching."""
    global _CURRENCY_MAP_CACHE, _CACHE_TIMESTAMP

    now = datetime.now(timezone.utc)

    # Fast path — no lock needed for a read when cache is warm
    if (
        _CACHE_TIMESTAMP
        and _CURRENCY_MAP_CACHE
        and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return _CURRENCY_MAP_CACHE

    async with _CACHE_LOCK:
        # Re-check after acquiring the lock — another coroutine may have
        # already populated the cache while we were waiting.
        now = datetime.now(timezone.utc)
        if (
            _CACHE_TIMESTAMP
            and _CURRENCY_MAP_CACHE
            and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
        ):
            return _CURRENCY_MAP_CACHE

    try:
        from sqlalchemy import select
        from app.models.field_mapping_model import CharacterMapping

        async with async_session_factory() as db:
            result = await db.execute(
                select(CharacterMapping).where(
                    CharacterMapping.category == "currency",
                    CharacterMapping.is_active.is_(True),
                )
            )
            mappings = result.scalars().all()

            if mappings:
                currency_map = {m.source_chars: m.target_chars for m in mappings}
                # Add lowercase variants
                extended_map = {}
                for k, v in currency_map.items():
                    extended_map[k] = v
                    extended_map[k.lower()] = v
                
                _CURRENCY_MAP_CACHE = extended_map
                _CACHE_TIMESTAMP = now
                logger.debug("Loaded %d currency mappings from DB", len(currency_map))
                return _CURRENCY_MAP_CACHE

    except Exception as e:
        logger.warning("Could not load currency map from DB: %s. Using defaults.", str(e))

    return _DEFAULT_CURRENCY_MAP


def _get_currency_map() -> dict[str, str]:
    """Get cached currency map synchronously."""
    if _CURRENCY_MAP_CACHE:
        return _CURRENCY_MAP_CACHE
    return _DEFAULT_CURRENCY_MAP


def invalidate_mapper_cache():
    """Clear the mapper configuration cache (call after config updates)."""
    global _CURRENCY_MAP_CACHE, _CACHE_TIMESTAMP
    _CURRENCY_MAP_CACHE = {}
    _CACHE_TIMESTAMP = None
    logger.info("Mapper configuration cache invalidated")


async def init_mapper_cache() -> None:
    """Initialize the mapper cache by loading currency mappings from DB.
    
    Call this at application startup or before first scrape.
    """
    await _load_currency_map()


# ───────── Price Parsing ─────────

_PRICE_PATTERN = re.compile(r"[\d\s.,]+")



def parse_price(raw: str | None) -> tuple[Decimal | None, str | None]:
    """Parse a price string like '250 000 €' into (Decimal(250000), 'EUR')."""
    if not raw:
        return None, None

    # Extract numeric part
    match = _PRICE_PATTERN.search(raw)
    if not match:
        return None, None

    # Strip all whitespace upfront — scraped HTML uses \xa0 (non-breaking space)
    # as a thousands separator; plain .replace(" ", "") misses it.
    num_str = re.sub(r"\s+", "", match.group())
    # Normalize: handle European decimal notation
    # "250000", "1.234,56" → "1234.56"
    if "," in num_str and "." in num_str:
        # European format: 1.234,56
        num_str = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        # Could be European decimal or thousands
        parts = num_str.split(",")
        if len(parts[-1]) == 2:
            # Likely decimal: 250,00
            num_str = num_str.replace(",", ".")
        else:
            # Likely thousands: 250,000
            num_str = num_str.replace(",", "")
    elif "." in num_str:
        # Could be decimal or European thousands (dots only)
        parts = num_str.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            # All groups after the first are 3 digits → thousand separators: 1.250.000
            num_str = num_str.replace(".", "")
        # else: single dot like 1250.50 — leave as-is (decimal)

    try:
        amount = Decimal(num_str)
    except Exception:
        logger.warning("Failed to parse price amount from: '%s'", raw)
        return None, None

    # Extract currency (from cache)
    currency_map = _get_currency_map()
    currency = "EUR"  # Default
    raw_lower = raw.lower()
    for symbol, code in currency_map.items():
        if symbol in raw_lower or symbol in raw:
            currency = code
            break

    return amount, currency


# ───────── Area Parsing ─────────

_AREA_PATTERN = re.compile(r"([\d\s.,]+)\s*m[²2]?", re.IGNORECASE)


def parse_area(raw: str | None) -> float | None:
    """Parse an area string like '120 m²' into 120.0."""
    if not raw:
        return None

    match = _AREA_PATTERN.search(raw)
    if not match:
        # Try just extracting a number
        num_match = re.search(r"[\d.,]+", raw)
        if num_match:
            try:
                return float(num_match.group().replace(",", ".").replace(" ", ""))
            except ValueError:
                return None
        return None

    num_str = match.group(1).strip().replace(" ", "")
    # Detect European thousands separator: digit(s) + dot + exactly 3 digits
    # e.g. "2.408" → 2408.0  but "120.5" → 120.5
    if re.match(r"^\d+\.\d{3}$", num_str):
        num_str = num_str.replace(".", "")
    else:
        num_str = num_str.replace(",", ".")
    try:
        return float(num_str)
    except ValueError:
        return None


# ───────── Integer Parsing ─────────

def parse_int(raw: str | None) -> int | None:
    """Parse integer from string, handling 'T3' → 3 for typology."""
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    if match:
        return int(match.group())
    return None


# ───────── Boolean Mapping ─────────

def parse_bool(raw: str | None) -> bool | None:
    """Map 'Yes'/truthy values to True, None/empty to None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    raw_lower = raw.strip().lower()
    if raw_lower in ("yes", "sim", "true", "1", "✓", "✔"):
        return True
    if raw_lower in ("no", "não", "false", "0"):
        return False
    # Numeric string: positive number → True (e.g. garage count "3" → has_garage=True)
    try:
        return float(raw_lower) > 0
    except ValueError:
        pass
    return None


# ───────── Date Parsing ─────────

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
]


def parse_date(raw: str | None) -> datetime | None:
    """Parse a date string in various formats."""
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    logger.warning("Failed to parse date: '%s'", raw)
    return None


# ───────── Typology → Bedrooms ─────────

def typology_to_bedrooms(typology: str | None) -> int | None:
    """Extract bedrooms from typology string.

    Handles:
    - T-series apartments: 'T3' → 3, 'T0' → 0
    - V-series villas (Portuguese moradias): 'V4' → 4, 'V3' → 3
    """
    if not typology:
        return None
    match = _TYPOLOGY_PATTERN.search(typology)
    if match:
        return int(match.group(1))
    return None


# ───────── Price per m² Calculation ─────────

def calculate_price_per_m2(
    price_amount: Decimal | None,
    area: float | None,
) -> Decimal | None:
    """Calculate price per m² from price and area."""
    if price_amount and area and area > 0:
        return Decimal(str(round(float(price_amount) / area, 2)))
    return None


def _normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _truncate_text(value: str | None, max_length: int) -> str | None:
    normalized = _normalize_whitespace(value)
    if normalized is None:
        return None
    return normalized[:max_length]


def _normalize_description_text(value: str | None) -> str | None:
    """Build a cleaned listing description while keeping the raw text untouched."""
    normalized = _normalize_whitespace(value)
    if not normalized:
        return None


    normalized = _CLEAN_DESC_PREFIX_PATTERN.sub("", normalized)
    normalized = _CLEAN_DESC_PUNCT_SPACES.sub(r"\1", normalized)
    normalized = _CLEAN_DESC_MISSING_SPACES.sub(r"\1 \2", normalized)
    normalized = _MULTIPLE_SPACES_PATTERN.sub(" ", normalized).strip()
    return normalized or None


def _looks_like_bad_address_fragment(value: str | None) -> bool:
    normalized = _normalize_whitespace(value)
    if not normalized:
        return False
    if len(normalized) > 100:
        return True
    sentence_markers = (". ", "!", "?", ":", " é ", " foi ", " com ")
    return any(marker in normalized.lower() for marker in sentence_markers)


def _normalize_habinedita_address(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    district = _truncate_text(raw.get("district"), 100)
    county = _truncate_text(raw.get("county"), 100)
    parish = _truncate_text(raw.get("parish"), 100)

    if _looks_like_bad_address_fragment(parish):
        parish = None

    location = _normalize_whitespace(raw.get("location"))
    if location:
        parts = [part.strip() for part in location.split(",") if part.strip()]
        if not district and parts:
            district = _truncate_text(parts[0], 100)
        if not county and len(parts) > 1:
            county = _truncate_text(parts[1], 100)

    return district, county, parish


# ═══════════════════════════════════════════════════════════
# Dispatcher Registration
# ═══════════════════════════════════════════════════════════

_PARTNER_NORMALIZERS: dict[str, Callable[[dict[str, Any]], PropertySchema]] = {}


def partner_normalizer(key: str) -> Callable:
    """Register a normalizer function in the dispatcher table."""
    def decorator(fn: Callable[[dict[str, Any]], PropertySchema]) -> Callable:
        _PARTNER_NORMALIZERS[key] = fn
        return fn
    return decorator


# ═══════════════════════════════════════════════════════════
# Shared Normalizer Helpers
# ═══════════════════════════════════════════════════════════

_NOT_SET = object()  # sentinel — distinguishes "not provided" from None

_LISTING_TYPE_RENT_KEYWORDS = ("arrend", "arrendar", "arrendamento", "rent", "rental", "aluguer")


def _infer_listing_type(raw: dict[str, Any], *, url_hint: str | None = None) -> str:
    """Infer 'rent' or 'sale' from raw payload fields or an optional URL hint."""
    val = (raw.get("business_type") or raw.get("business_state") or "").lower()
    if any(w in val for w in _LISTING_TYPE_RENT_KEYWORDS):
        return "rent"
    if url_hint and "/Arrendamento/" in url_hint:
        return "rent"
    return "sale"


def _infer_property_type_from_title(
    title: str | None,
    known_types: tuple[str, ...],
    *,
    startswith: bool = True,
) -> str | None:
    """Match the first known type against a listing title.

    Args:
        startswith: True  → title must begin with the type (strict, e.g. habinedita).
                    False → type may appear anywhere in the title (loose, e.g. habita).
    """
    if not title:
        return None
    lower = title.strip().lower()
    for pt in known_types:
        if lower.startswith(pt.lower()) if startswith else pt.lower() in lower:
            return pt
    return None


def _build_base_schema(
    raw: dict[str, Any],
    *,
    source_partner: str,
    listing_type: str,
    property_type: str | None,
    partner_id: str | None,
    address: Address,
    area_useful: float | None,
    area_gross: float | None,
    area_land: float | None,
    # Optional overrides — if omitted, values are read/computed from raw
    bedrooms: int | None | object = _NOT_SET,
    title: str | None | object = _NOT_SET,
    floor: str | None = None,
    construction_year: int | None = None,
    seo: dict[str, Any] | None = None,
    advertiser: str | None = None,
    contacts: str | None = None,
    extra_flags: dict[str, Any] | None = None,
    raw_partner_payload: dict[str, Any] | None = None,
) -> PropertySchema:
    """Build a PropertySchema from pre-processed partner-specific values.

    All partner-specific parsing (address, listing_type, property_type, …) must be
    resolved before calling this. This function owns only the logic that is identical
    across all partners.
    """
    if bedrooms is _NOT_SET:
        _b = parse_int(raw.get("bedrooms"))
        bedrooms = _b if _b is not None else typology_to_bedrooms(raw.get("typology"))

    if title is _NOT_SET:
        title = raw.get("title")

    price_amount, price_currency = parse_price(raw.get("price"))
    price_per_m2_amount = calculate_price_per_m2(price_amount, area_gross or area_useful)
    raw_description = raw.get("raw_description")

    return PropertySchema(
        partner_id=partner_id,
        source_partner=source_partner,
        source_url=raw.get("url"),
        title=title,  # type: ignore[arg-type]
        listing_type=listing_type,
        property_type=property_type,
        typology=raw.get("typology"),
        bedrooms=bedrooms,  # type: ignore[arg-type]
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=floor,
        construction_year=construction_year,
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=area_useful,
        area_gross_m2=area_gross,
        area_land_m2=area_land,
        address=address,
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(raw.get("images", []), raw.get("alt_texts", []))
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
            **(extra_flags or {}),
        ),
        descriptions={k: v for k, v in {
            "raw": raw_description,
            "pt": _normalize_description_text(raw_description),
        }.items() if v},
        seo=seo or None,
        energy_certificate=raw.get("energy_certificate"),
        advertiser=advertiser,
        contacts=contacts,
        raw_partner_payload=raw_partner_payload if raw_partner_payload is not None else raw,
    )


# ═══════════════════════════════════════════════════════════
# EGO RealEstate Platform
# ═══════════════════════════════════════════════════════════

_EGO_PROPERTY_TYPES = (
    "Moradia Geminada", "Moradia", "Apartamento", "Loja",
    "Escritório", "Armazém", "Terreno", "Garagem", "Lote", "Quintal", "Quinta",
)


def _normalize_ego_address(raw: dict[str, Any]) -> tuple[Address, str | None]:
    """Build an Address from an EGO RealEstate platform payload.

    EGO location field uses ">" as the hierarchy delimiter, e.g.:
        "District > County > Parish"  (3-part)
        "County > Parish"             (2-part)
    Explicit district/county/parish keys take priority when present.

    Returns:
        (Address, region_fallback) where region_fallback equals city when no
        explicit district is available (EGO often omits the district level).
    """
    region = _truncate_text(raw.get("district") or None, 100)
    city = _truncate_text(raw.get("county") or None, 100)
    area_parish = _truncate_text(raw.get("parish") or None, 100)
    location_raw = _normalize_whitespace(raw.get("location") or "") or ""

    if not city and location_raw:
        parts = [p.strip() for p in location_raw.split(">")]
        if len(parts) >= 3:
            region = _truncate_text(parts[0] or None, 100)
            city = _truncate_text(parts[1] or None, 100)
            if not area_parish:
                area_parish = _truncate_text(parts[-1] or None, 100)
        else:
            city = _truncate_text(parts[0] or None, 100)
            if len(parts) > 1 and not area_parish:
                area_parish = _truncate_text(parts[-1] or None, 100)

    # EGO often omits the district — fall back to city level as region
    region_for_schema = region or city

    return Address(
        country="Portugal",
        region=region_for_schema,
        city=city,
        area=area_parish,
        full_address=_truncate_text(location_raw, 500) or None,
    ), region_for_schema


def normalize_ego_platform_payload(raw: dict[str, Any], source_partner: str) -> PropertySchema:
    """Normalize an EGO RealEstate platform payload into canonical PropertySchema.

    Shared by all EGO-based partners (t2mais1, imobiliariaprp, …).
    To register a new EGO partner add one line to the Partner Normalizers section:

        @partner_normalizer("new_partner")
        def normalize_new_partner_payload(raw: dict[str, Any]) -> PropertySchema:
            return normalize_ego_platform_payload(raw, "new_partner")
    """
    useful_area = parse_area(raw.get("area") or raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))

    # EGO injects a "Ref. " text prefix — strip it
    partner_id: str | None = raw.get("property_id") or raw.get("reference")
    if partner_id and isinstance(partner_id, str):
        partner_id = _EGO_REF_PATTERN.sub("", partner_id).strip() or None
        # partner_id = _EGO_REF_PATTERN.search(partner_id).strip() or None   
    # Infer property type from title when not explicitly scraped
    title = raw.get("title") or ""
    property_type = (
        raw.get("property_type")
        or _infer_property_type_from_title(title, _EGO_PROPERTY_TYPES)
    )
    # Fallback: typology codes in title → Apartamento / Moradia
    if not property_type:
        if re.search(r"\bT\d+\b", title, re.IGNORECASE):
            property_type = "Apartamento"
        elif re.search(r"\bV\d+\b", title, re.IGNORECASE):
            property_type = "Moradia"

    address, _ = _normalize_ego_address(raw)

    return _build_base_schema(
        raw,
        source_partner=source_partner,
        listing_type=_infer_listing_type(raw),
        property_type=property_type,
        partner_id=partner_id,
        address=address,
        area_useful=useful_area,
        area_gross=gross_area,
        area_land=parse_area(raw.get("land_area")),
        floor=raw.get("floor"),
        construction_year=parse_int(raw.get("construction_year")),
    )


# ═══════════════════════════════════════════════════════════
# Partner Normalizers
# ═══════════════════════════════════════════════════════════

@partner_normalizer("pearls")
def normalize_pearls_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Pearls of Portugal payload into canonical PropertySchema."""
    return _build_base_schema(
        raw,
        source_partner="pearls",
        listing_type=_infer_listing_type(raw),
        property_type=raw.get("property_type"),
        partner_id=raw.get("property_id") or raw.get("reference"),
        address=Address(
            country="Portugal",
            region=raw.get("district"),
            city=raw.get("county"),
            area=raw.get("parish"),
        ),
        area_useful=parse_area(raw.get("useful_area")),
        area_gross=parse_area(raw.get("gross_area")),
        area_land=parse_area(raw.get("land_area")),
        floor=raw.get("floor"),
        construction_year=parse_int(raw.get("construction_year")),
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
    )

_HABINEDITA_PROPERTY_TYPES = (
    "Moradia Geminada", "Moradia", "Apartamento", "Loja",
    "Escritório", "Armazém", "Terreno", "Garagem", "Quintal",
)


@partner_normalizer("habinedita")
def normalize_habinedita_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Habinédita payload into canonical PropertySchema."""
    district, county, parish = _normalize_habinedita_address(raw)
    condition = _normalize_whitespace(raw.get("condition"))

    is_new_construction: bool | None = None
    if condition and any(m in condition.lower() for m in ("novo", "new", "constru", "em planta")):
        is_new_construction = True

    seo = {k: v for k, v in {
        "page_title": raw.get("page_title"),
        "meta_description": raw.get("meta_description"),
        "headers": raw.get("headers"),
    }.items() if v}

    return _build_base_schema(
        raw,
        source_partner="habinedita",
        listing_type=_infer_listing_type(raw),
        property_type=(
            raw.get("property_type")
            or _infer_property_type_from_title(raw.get("title"), _HABINEDITA_PROPERTY_TYPES)
        ),
        partner_id=raw.get("property_id"),
        address=Address(
            country="Portugal",
            region=district,
            city=county,
            area=parish,
            full_address=_truncate_text(raw.get("full_address"), 500),
        ),
        area_useful=parse_area(raw.get("useful_area")),
        area_gross=parse_area(raw.get("gross_area")),
        area_land=parse_area(raw.get("land_area")),
        floor=raw.get("floor"),
        construction_year=parse_int(raw.get("construction_year")),
        seo=seo or None,
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
        extra_flags={"is_new_construction": is_new_construction} if is_new_construction is not None else None,
    )

_HABITA_PROPERTY_TYPES = (
    "Moradia Geminada", "Moradia", "Apartamento", "Loja",
    "Escritório", "Armazém", "Terreno", "Garagem",
)


@partner_normalizer("habita")
def normalize_habita_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Habita payload into canonical PropertySchema."""
    location = _normalize_whitespace(raw.get("location") or "") or ""

    # Prefer explicit selector values; fall back to parsing the location string
    region: str | None = raw.get("district") or None
    city: str | None = raw.get("county") or None
    area_parish: str | None = raw.get("parish") or None

    if not region and not city:
        if "," in location:
            parts = [p.strip() for p in location.split(",")]
            region = parts[0] or None
            city = parts[-1] or None
        elif ">" in location:
            parts = [p.strip() for p in location.split(">")]
            if len(parts) >= 3:
                region = parts[0] or None
                city = parts[1] or None
                area_parish = area_parish or (parts[-1] or None)
            else:
                city = parts[0] or None
                if len(parts) > 1:
                    area_parish = area_parish or (parts[-1] or None)
        else:
            city = location or None
    elif not city and location:
        if ">" in location:
            parts = [p.strip() for p in location.split(">")]
            if len(parts) >= 3:
                region = parts[0] or None
                city = parts[1] or None
                area_parish = area_parish or (parts[-1] or None)
            else:
                city = parts[0] or None
                if len(parts) > 1:
                    area_parish = area_parish or (parts[-1] or None)
        else:
            city = location or None

    return _build_base_schema(
        raw,
        source_partner="habita",
        listing_type=_infer_listing_type(raw),
        property_type=(
            raw.get("property_type")
            or _infer_property_type_from_title(raw.get("title"), _HABITA_PROPERTY_TYPES, startswith=False)
        ),
        partner_id=raw.get("property_id"),
        address=Address(
            country="Portugal",
            region=region,
            city=city,
            area=area_parish,
            full_address=_truncate_text(location, 500) or None,
        ),
        area_useful=parse_area(raw.get("useful_area")),
        area_gross=parse_area(raw.get("gross_area")),
        area_land=parse_area(raw.get("land_area")),
    )


@partner_normalizer("brightmangroup_pt")
def normalize_brightmangroup_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Brightman Group payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "brightmangroup_pt")


@partner_normalizer("t2mais1")
def normalize_t2mais1_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw T2+1 (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "t2mais1")


@partner_normalizer("imobiliariaprp")
def normalize_imobiliariaprp_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize an Imobiliária PRP (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "imobiliariaprp")


@partner_normalizer("escolhacerta")
def normalize_escolhacerta_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize an Escolha Certa (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "escolhacerta")


@partner_normalizer("sottomayor")
def normalize_sottomayor_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Sottomayor Properties (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "sottomayor")


@partner_normalizer("barcelcasa")
def normalize_barcelcasa_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Barcelcasa Imobiliária (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "barcelcasa")


@partner_normalizer("sunpoint")
def normalize_sunpoint_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Sunpoint Properties (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "sunpoint")

@partner_normalizer("casa10_pt")
def normalize_casa10_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a casa10_pt Properties (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "casa10_pt")
@partner_normalizer("entreparedes")
def normalize_entreparedes_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a entreparedes Properties (EGO RealEstate platform) payload into canonical PropertySchema."""
    return normalize_ego_platform_payload(raw, "entreparedes")

@partner_normalizer("realkey")
def normalize_realkey_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Realkey (Centralimo platform) payload into canonical PropertySchema."""
    source_url = raw.get("url") or ""
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    location_raw = _normalize_whitespace(raw.get("location") or "") or ""

    # Partner ID: "#property-id" emits "Referência: 1308" — strip the label prefix
    partner_id_raw = raw.get("property_id") or ""
    partner_id = re.sub(r"^[^:]+:\s*", "", partner_id_raw).strip() or None

    # Title: h1.property-title embeds location inside a <small> — strip it
    title_full = (raw.get("title") or "").strip()
    clean_title = title_full.replace(location_raw, "").strip() if location_raw and location_raw in title_full else title_full

    # Condition: "Estado: Em construção" — strip the label prefix, store on raw_payload
    condition_raw = raw.get("condition") or ""
    condition = re.sub(r"^[^:]+:\s*", "", condition_raw).strip() or None

    # Centralimo has no typology field — bedrooms must be inferred from the title
    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(clean_title or raw.get("title"))

    # Typology: infer from title when not scraped directly
    typology = raw.get("typology")
    if not typology and clean_title:
        typ_match = re.search(r"\b([TV]\d+)\b", clean_title, re.IGNORECASE)
        if typ_match:
            typology = typ_match.group(1).upper()

    # Location: prefer URL path (most reliable) then fall back to location field
    # URL pattern: /Imovel/{listing_type}/{property_type}/{district}/{county}/{parish}/{id}
    region: str | None = None
    city: str | None = None
    area_parish: str | None = None
    # url_match = re.search(
    #     r"/Imovel/[^/]+/[^/]+/([^/?#]+)/([^/?#]+)/([^/?#]+)/\d+",
    #     source_url,
    # )
    url_match = _URL_LOCATION_PATTERN.search(source_url)
    if url_match:
        region = url_match.group(1).replace("-", " ")  # district
        city = url_match.group(2).replace("-", " ")    # county
        area_parish = url_match.group(3).replace("-", " ")  # parish
    elif "," in location_raw:
        parts = [p.strip() for p in location_raw.split(",")]
        area_parish = parts[0] or None
        city = parts[-1] or None
    else:
        city = location_raw or None

    return _build_base_schema(
        raw,
        source_partner="realkey",
        listing_type=_infer_listing_type(raw, url_hint=source_url),
        property_type=raw.get("property_type"),
        partner_id=partner_id,
        address=Address(
            country="Portugal",
            region=region,
            city=city,
            area=area_parish,
            full_address=_truncate_text(location_raw, 500) or None,
        ),
        area_useful=useful_area,
        area_gross=gross_area,
        area_land=parse_area(raw.get("land_area")),
        construction_year=parse_int(raw.get("construction_year")),
        bedrooms=bedrooms,
        title=clean_title or None,
        raw_partner_payload={**raw, "condition": condition, "typology": typology} if (condition or typology) else None,
    )

@partner_normalizer("mysquare")
def normalize_mysquare_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw mysquare payload into canonical PropertySchema."""
    district, county, parish = _normalize_habinedita_address(raw)
    condition = _normalize_whitespace(raw.get("condition"))

    is_new_construction: bool | None = None
    if condition and any(m in condition.lower() for m in ("novo", "new", "constru", "em planta")):
        is_new_construction = True

    seo = {k: v for k, v in {
        "page_title": raw.get("page_title"),
        "meta_description": raw.get("meta_description"),
        "headers": raw.get("headers"),
    }.items() if v}

    return _build_base_schema(
        raw,
        source_partner="mysquare",
        listing_type=_infer_listing_type(raw),
        property_type=(
            raw.get("property_type")
            or _infer_property_type_from_title(raw.get("title"), _HABINEDITA_PROPERTY_TYPES)
        ),
        partner_id=raw.get("property_id"),
        address=Address(
            country="Portugal",
            region=district,
            city=county,
            area=parish,
            full_address=_truncate_text(raw.get("full_address"), 500),
        ),
        area_useful=parse_area(raw.get("useful_area")),
        area_gross=parse_area(raw.get("gross_area")),
        area_land=parse_area(raw.get("land_area")),
        floor=raw.get("floor"),
        construction_year=parse_int(raw.get("construction_year")),
        seo=seo or None,
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
        extra_flags={"is_new_construction": is_new_construction} if is_new_construction is not None else None,
    )

def normalize_partner_payload(raw: dict[str, Any], partner: str) -> PropertySchema:
    """Dispatch normalization to the appropriate partner normalizer."""
    normalizer = _PARTNER_NORMALIZERS.get(partner)
    if not normalizer:
        raise ValueError(f"No normalizer registered for partner: '{partner}'")
    return normalizer(raw)


# ───────── PropertySchema → DB model fields ─────────

def schema_to_listing_dict(schema: PropertySchema, scrape_job_id: UUID | None = None) -> dict[str, Any]:
    """Convert a canonical PropertySchema to a dict suitable for creating a Listing ORM model."""
    listing_dict = {
        "partner_id": schema.partner_id,
        "source_partner": schema.source_partner,
        "source_url": str(schema.source_url) if schema.source_url else None,
        "title": schema.title,
        "listing_type": schema.listing_type,
        "property_type": schema.property_type,
        "typology": schema.typology,
        "bedrooms": schema.bedrooms,
        "bathrooms": schema.bathrooms,
        "floor": schema.floor,
        "price_amount": Decimal(str(schema.price.amount)) if schema.price.amount else None,
        "price_currency": schema.price.currency or "EUR",
        "price_per_m2": Decimal(str(schema.price_per_m2.amount)) if schema.price_per_m2 and schema.price_per_m2.amount else None,
        "area_useful_m2": schema.area_useful_m2,
        "area_gross_m2": schema.area_gross_m2,
        "area_land_m2": schema.area_land_m2,
        "district": schema.address.region,
        "county": schema.address.city,
        "parish": schema.address.area,
        "full_address": schema.address.full_address,
        "latitude": schema.latitude,
        "longitude": schema.longitude,
        "has_garage": schema.features.has_garage,
        "has_elevator": schema.features.has_elevator,
        "has_balcony": schema.features.has_balcony,
        "has_air_conditioning": schema.features.has_air_conditioning,
        "has_pool": schema.features.has_pool,
        "energy_certificate": schema.energy_certificate,
        "construction_year": schema.construction_year,
        "advertiser": schema.advertiser,
        "contacts": schema.contacts,
        "raw_description": schema.descriptions.get("raw"),
        "description": schema.descriptions.get("pt"),
        "description_quality_score": schema.description_quality_score,
        "page_title": schema.seo.get("page_title") if schema.seo else None,
        "headers": schema.seo.get("headers") if schema.seo else None,
        "meta_description": schema.seo.get("meta_description") if schema.seo else None,
        "raw_payload": schema.raw_partner_payload,
        "scrape_job_id": scrape_job_id,
    }

    for field, max_length in _LISTING_STRING_LIMITS.items():
        listing_dict[field] = _truncate_text(listing_dict.get(field), max_length)

    return listing_dict
