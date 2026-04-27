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
from datetime import datetime, timezone
from decimal import Decimal
from collections.abc import Callable
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

    num_str = match.group(1).strip().replace(" ", "").replace(",", ".")
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
    match = re.search(r"[TtVv](\d+)", typology)
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

    normalized = re.sub(r"^(descriç[aã]o|description)\s*[:\-]?\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([.!?;:])(\S)", r"\1 \2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
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


# ───────── Partner Normalizers ─────────

_PARTNER_NORMALIZERS: dict[str, Callable[[dict[str, Any]], PropertySchema]] = {}


def partner_normalizer(key: str) -> Callable:
    """Register a normalizer function in the dispatcher table."""
    def decorator(fn: Callable[[dict[str, Any]], PropertySchema]) -> Callable:
        _PARTNER_NORMALIZERS[key] = fn
        return fn
    return decorator


@partner_normalizer("pearls")
def normalize_pearls_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Pearls of Portugal payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    land_area = parse_area(raw.get("land_area"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("typology"))
    
    # Map business_type to listing_type
    business_type = (raw.get("business_type") or "").lower().strip()
    listing_type = "sale"  # Default
    if business_type in ("rent", "rental", "arrendar", "arrendamento"):
        listing_type = "rent"
    elif business_type in ("buy", "sale", "venda", "comprar"):
        listing_type = "sale"

    price_per_m2_amount = calculate_price_per_m2(
        price_amount, gross_area or useful_area
    )
    raw_description = raw.get("raw_description")
    normalized_description = _normalize_description_text(raw_description)

    return PropertySchema(
        partner_id=raw.get("property_id") or raw.get("reference"),
        source_partner="pearls",
        source_url=raw.get("url"),
        title=raw.get("title"),
        listing_type=listing_type,
        property_type=raw.get("property_type"),
        typology=raw.get("typology"),
        bedrooms=bedrooms,
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=raw.get("floor"),
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=useful_area,
        area_gross_m2=gross_area,
        area_land_m2=land_area,
        address=Address(
            country="Portugal",
            region=raw.get("district"),
            city=raw.get("county"),
            area=raw.get("parish"),
        ),
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(
                raw.get("images", []),
                raw.get("alt_texts", []),
            )
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
        ),
        descriptions={
            key: value
            for key, value in {
                "raw": raw_description,
                "pt": normalized_description,
            }.items()
            if value
        },
        energy_certificate=raw.get("energy_certificate"),
        construction_year=parse_int(raw.get("construction_year")),
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
        raw_partner_payload=raw,
    )

@partner_normalizer("habinedita")
def normalize_habinedita_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Habinédita payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    land_area = parse_area(raw.get("land_area"))
    district, county, parish = _normalize_habinedita_address(raw)
    condition = _normalize_whitespace(raw.get("condition"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("typology"))

    business_type = (raw.get("business_type") or "").lower().strip()
    listing_type = "sale"
    if business_type in ("rent", "rental", "arrendar", "arrendamento"):
        listing_type = "rent"

    price_per_m2_amount = calculate_price_per_m2(price_amount, gross_area or useful_area)
    raw_description = raw.get("raw_description")
    normalized_description = _normalize_description_text(raw_description)
    seo = {
        "page_title": raw.get("page_title"),
        "meta_description": raw.get("meta_description"),
        "headers": raw.get("headers"),
    }
    seo = {key: value for key, value in seo.items() if value}
    is_new_construction = None
    if condition:
        normalized_condition = condition.lower()
        if any(marker in normalized_condition for marker in ("novo", "new", "constru", "em planta")):
            is_new_construction = True

    # Derive property_type from title when not set by selectors/summary.
    # Habinedita titles always start with the type: "Apartamento T3", etc.
    property_type = raw.get("property_type")
    if not property_type and raw.get("title"):
        _HABINEDITA_TYPES = (
            "Moradia Geminada", "Moradia", "Apartamento", "Loja",
            "Escritório", "Armazém", "Terreno", "Garagem", "Quintal",
        )
        title_lower = raw["title"].strip().lower()
        for pt in _HABINEDITA_TYPES:
            if title_lower.startswith(pt.lower()):
                property_type = pt
                break

    return PropertySchema(
        partner_id=raw.get("property_id"),
        source_partner="habinedita",
        source_url=raw.get("url"),
        title=raw.get("title"),
        listing_type=listing_type,
        property_type=property_type,
        typology=raw.get("typology"),
        bedrooms=bedrooms,
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=raw.get("floor"),
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=useful_area,
        area_gross_m2=gross_area,
        area_land_m2=land_area,
        address=Address(
            country="Portugal",
            region=district,
            city=county,
            area=parish,
            full_address=_truncate_text(raw.get("full_address"), 500),
        ),
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(
                raw.get("images", []),
                raw.get("alt_texts", []),
            )
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
            is_new_construction=is_new_construction,
        ),
        descriptions={
            key: value
            for key, value in {
                "raw": raw_description,
                "pt": normalized_description,
            }.items()
            if value
        },
        seo=seo or None,
        energy_certificate=raw.get("energy_certificate"),
        construction_year=parse_int(raw.get("construction_year")),
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
        raw_partner_payload=raw,
    )


# ───────── Dispatcher ─────────

@partner_normalizer("habita")
def normalize_habita_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Habita payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    land_area = parse_area(raw.get("land_area"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("typology"))

    business_type_raw = (raw.get("business_type") or "").lower().strip()
    listing_type = "rent" if any(
        w in business_type_raw for w in ("arrend", "arrendar", "rent", "aluguer")
    ) else "sale"

    # Location: prefer direct selector values (district_selector, county_selector);
    # fall back to parsing ".wb-fld-location" which may contain "County > Parish"
    # or "District, City" depending on the listing.
    location = _normalize_whitespace(raw.get("location") or "") or ""
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
                # "District > County > Parish" format
                region = parts[0] or None
                city = parts[1] or None
                area_parish = parts[-1] or None
            else:
                city = parts[0] or None
                if len(parts) > 1:
                    area_parish = parts[-1] or None
        else:
            city = location or None
    elif not city and location:
        if ">" in location:
            parts = [p.strip() for p in location.split(">")]
            if len(parts) >= 3:
                region = parts[0] or None
                city = parts[1] or None
                area_parish = parts[-1] or None
            else:
                city = parts[0] or None
                if len(parts) > 1:
                    area_parish = parts[-1] or None
        elif city is None:
            city = location or None

    property_type = raw.get("property_type")
    if not property_type and raw.get("title"):
        _HABITA_TYPES = (
            "Moradia Geminada", "Moradia", "Apartamento", "Loja",
            "Escritório", "Armazém", "Terreno", "Garagem",
        )
        title_lower = raw["title"].strip().lower()
        for pt in _HABITA_TYPES:
            if pt.lower() in title_lower:
                property_type = pt
                break

    price_per_m2_amount = calculate_price_per_m2(price_amount, gross_area or useful_area)
    raw_description = raw.get("raw_description")
    normalized_description = _normalize_description_text(raw_description)

    return PropertySchema(
        partner_id=raw.get("property_id"),
        source_partner="habita",
        source_url=raw.get("url"),
        title=raw.get("title"),
        listing_type=listing_type,
        property_type=property_type,
        typology=raw.get("typology"),
        bedrooms=bedrooms,
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=None,
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=useful_area,
        area_gross_m2=gross_area,
        area_land_m2=land_area,
        address=Address(
            country="Portugal",
            region=region,
            city=city,
            area=area_parish,
            full_address=_truncate_text(location, 500) or None,
        ),
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(
                raw.get("images", []),
                raw.get("alt_texts", []),
            )
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
        ),
        descriptions={
            key: value
            for key, value in {
                "raw": raw_description,
                "pt": normalized_description,
            }.items()
            if value
        },
        seo=None,
        energy_certificate=raw.get("energy_certificate"),
        raw_partner_payload=raw,
    )


@partner_normalizer("t2mais1")
def normalize_t2mais1_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw T2+1 (EgoRealEstate platform) payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("area") or raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    land_area = parse_area(raw.get("land_area"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("typology"))

    # EgoRealEstate: business field uses "Venda"/"Arrendamento"
    business_type_raw = (raw.get("business_type") or raw.get("business_state") or "").lower().strip()
    listing_type = "rent" if any(
        w in business_type_raw for w in ("arrend", "arrendar", "rent", "aluguer")
    ) else "sale"

    # Location: ".wb-fld-location" get_text() returns e.g.
    # "Santiago do Cacém > Santiago do Cacém, S.Cruz e S.Bartolomeu da Serra"
    # Split on ">" — first part is county, last part is parish
    location_raw = _normalize_whitespace(raw.get("location") or "") or ""
    region: str | None = raw.get("district") or None
    city: str | None = raw.get("county") or None
    area_parish: str | None = raw.get("parish") or None

    if not city and location_raw:
        parts = [p.strip() for p in location_raw.split(">")]
        if len(parts) >= 3:
            # "District > County > Parish" format
            region = parts[0] or None
            city = parts[1] or None
            area_parish = parts[-1] or None
        else:
            city = parts[0] or None
            if len(parts) > 1:
                area_parish = parts[-1] or None

    # Fall back: use city as region (district) when no explicit district available
    if not region:
        region = city

    # Reference: strip "Ref. " prefix added by the template
    partner_id = raw.get("property_id") or raw.get("reference")
    if partner_id and isinstance(partner_id, str):
        partner_id = re.sub(r"^Ref\.\s*", "", partner_id).strip() or None

    # Property type: infer from title when not extracted directly
    property_type = raw.get("property_type")
    if not property_type and raw.get("title"):
        _T2MAIS1_TYPES = (
            "Moradia Geminada", "Moradia", "Apartamento", "Loja",
            "Escritório", "Armazém", "Terreno", "Garagem", "Lote",
            "Quintal", "Quinta",
        )
        title_stripped = raw["title"].strip()
        title_lower = title_stripped.lower()
        for pt in _T2MAIS1_TYPES:
            if pt.lower() in title_lower:
                property_type = pt
                break
        # Infer from typology-style titles: "T2 mobilado" → Apartamento, "V3 ..." → Moradia
        if not property_type:
            if re.search(r"\bT\d+\b", title_stripped, re.IGNORECASE):
                property_type = "Apartamento"
            elif re.search(r"\bV\d+\b", title_stripped, re.IGNORECASE):
                property_type = "Moradia"

    price_per_m2_amount = calculate_price_per_m2(price_amount, gross_area or useful_area)
    raw_description = raw.get("raw_description")
    normalized_description = _normalize_description_text(raw_description)

    return PropertySchema(
        partner_id=partner_id,
        source_partner="t2mais1",
        source_url=raw.get("url"),
        title=raw.get("title"),
        listing_type=listing_type,
        property_type=property_type,
        typology=raw.get("typology"),
        bedrooms=bedrooms,
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=raw.get("floor"),
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=useful_area,
        area_gross_m2=gross_area,
        area_land_m2=land_area,
        address=Address(
            country="Portugal",
            region=region,
            city=city,
            area=area_parish,
            full_address=_truncate_text(location_raw, 500) or None,
        ),
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(
                raw.get("images", []),
                raw.get("alt_texts", []),
            )
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
        ),
        descriptions={
            key: value
            for key, value in {
                "raw": raw_description,
                "pt": normalized_description,
                "meta": raw.get("meta_description"),
            }.items()
            if value
        },
        energy_certificate=raw.get("energy_certificate"),
        construction_year=parse_int(raw.get("construction_year")),
        raw_partner_payload=raw,
    )


@partner_normalizer("imobiliariaprp")
def normalize_imobiliariaprp_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize an Imobiliária PRP (EgoRealEstate platform) payload into canonical PropertySchema."""
    schema = normalize_t2mais1_payload(raw)
    return schema.model_copy(update={"source_partner": "imobiliariaprp"})


@partner_normalizer("escolhacerta")
def normalize_escolhacerta_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize an Escolha Certa (EgoRealEstate platform) payload into canonical PropertySchema."""
    schema = normalize_t2mais1_payload(raw)
    return schema.model_copy(update={"source_partner": "escolhacerta"})


@partner_normalizer("sottomayor")
def normalize_sottomayor_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Sottomayor Properties (EgoRealEstate platform) payload into canonical PropertySchema."""
    schema = normalize_t2mais1_payload(raw)
    return schema.model_copy(update={"source_partner": "sottomayor"})


@partner_normalizer("barcelcasa")
def normalize_barcelcasa_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Barcelcasa Imobiliária (EgoRealEstate platform) payload into canonical PropertySchema."""
    schema = normalize_t2mais1_payload(raw)
    return schema.model_copy(update={"source_partner": "barcelcasa"})


@partner_normalizer("sunpoint")
def normalize_sunpoint_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a Sunpoint Properties (EgoRealEstate platform) payload into canonical PropertySchema."""
    schema = normalize_t2mais1_payload(raw)
    return schema.model_copy(update={"source_partner": "sunpoint"})


@partner_normalizer("realkey")
def normalize_realkey_payload(raw: dict[str, Any]) -> PropertySchema:
    """Normalize a raw Realkey (Centralimo platform) payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("title"))

    # Listing type: infer from source URL (/Compra/ → sale, /Arrendamento/ → rent)
    source_url = raw.get("url") or ""
    if "/Arrendamento/" in source_url:
        listing_type = "rent"
    else:
        listing_type = "sale"

    # Title: h1.property-title contains <small> with location — strip it
    title_full = (raw.get("title") or "").strip()
    location_raw = _normalize_whitespace(raw.get("location") or "") or ""
    if location_raw and location_raw in title_full:
        title = title_full.replace(location_raw, "").strip()
    else:
        title = title_full

    # Property ID: "#property-id" → "Referência: 1308" — strip label
    partner_id: str | None = raw.get("property_id") or ""
    if partner_id:
        partner_id = re.sub(r"^[^:]+:\s*", "", partner_id).strip() or None
    else:
        partner_id = None

    # Condition: ".property-features li:has(.fa-certificate)" → "Estado: Em construção"
    condition_raw = raw.get("condition") or ""
    condition = re.sub(r"^[^:]+:\s*", "", condition_raw).strip() or None

    # Typology: realkey HTML does not expose a dedicated typology field.
    # Infer from title: "Apartamento T2 no centro do Porto" → "T2"
    typology = raw.get("typology")
    if not typology and title:
        typ_match = re.search(r"\b([TV]\d+)\b", title, re.IGNORECASE)
        if typ_match:
            typology = typ_match.group(1).upper()

    # Location: extract district + county from URL path (most reliable source)
    # URL pattern: /pt-PT/Imovel/{listing_type}/{property_type}/{district}/{county}/{parish}/{id}
    region: str | None = None
    city: str | None = None
    area_parish: str | None = None
    url_match = re.search(
        r"/Imovel/[^/]+/[^/]+/([^/?#]+)/([^/?#]+)/([^/?#]+)/\d+",
        source_url,
    )
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

    price_per_m2_amount = calculate_price_per_m2(price_amount, useful_area or gross_area)
    raw_description = raw.get("raw_description")
    normalized_description = _normalize_description_text(raw_description)

    return PropertySchema(
        partner_id=partner_id,
        source_partner="realkey",
        source_url=source_url or None,
        title=title or None,
        listing_type=listing_type,
        property_type=raw.get("property_type"),
        typology=typology,
        bedrooms=bedrooms,
        bathrooms=parse_int(raw.get("bathrooms")),
        floor=None,
        construction_year=parse_int(raw.get("construction_year")),
        price=Money(
            amount=float(price_amount) if price_amount else None,
            currency=price_currency,
        ),
        price_per_m2=Money(
            amount=float(price_per_m2_amount) if price_per_m2_amount else None,
            currency=price_currency,
        ) if price_per_m2_amount else None,
        area_useful_m2=useful_area,
        area_gross_m2=gross_area,
        area_land_m2=parse_area(raw.get("land_area")),
        address=Address(
            country="Portugal",
            region=region,
            city=city,
            area=area_parish,
            full_address=_truncate_text(location_raw, 500) or None,
        ),
        media=[
            MediaAsset(url=url, alt_text=alt, type="photo")
            for url, alt in zip(
                raw.get("images", []),
                raw.get("alt_texts", []),
            )
        ],
        features=ListingFlags(
            has_garage=parse_bool(raw.get("garage")),
            has_elevator=parse_bool(raw.get("elevator")),
            has_balcony=parse_bool(raw.get("balcony")),
            has_air_conditioning=parse_bool(raw.get("air_conditioning")),
            has_pool=parse_bool(raw.get("swimming_pool")),
        ),
        descriptions={
            key: value
            for key, value in {
                "raw": raw_description,
                "pt": normalized_description,
            }.items()
            if value
        },
        energy_certificate=raw.get("energy_certificate"),
        raw_partner_payload={**raw, "condition": condition} if condition else raw,
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
