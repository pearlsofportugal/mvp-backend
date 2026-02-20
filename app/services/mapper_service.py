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
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
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

_CURRENCY_MAP_CACHE: Dict[str, str] = {}
_CACHE_TIMESTAMP: Optional[datetime] = None
_CACHE_TTL_SECONDS = 300  # 5 minutes

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


async def _load_currency_map() -> Dict[str, str]:
    """Load currency symbol mappings from DB with caching."""
    global _CURRENCY_MAP_CACHE, _CACHE_TIMESTAMP

    now = datetime.now(timezone.utc)

    # Check cache validity
    if (
        _CACHE_TIMESTAMP
        and _CURRENCY_MAP_CACHE
        and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return _CURRENCY_MAP_CACHE

    try:
        from sqlalchemy import select
        from app.models.field_mapping import CharacterMapping

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


def _get_currency_map() -> Dict[str, str]:
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



def parse_price(raw: Optional[str]) -> Tuple[Optional[Decimal], Optional[str]]:
    """Parse a price string like '250 000 €' into (Decimal(250000), 'EUR')."""
    if not raw:
        return None, None

    # Extract numeric part
    match = _PRICE_PATTERN.search(raw)
    if not match:
        return None, None

    num_str = match.group().strip()
    # Normalize: remove spaces, handle European decimal notation
    # "250 000" → "250000", "1.234,56" → "1234.56"
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
        num_str = num_str.replace(" ", "")
    else:
        num_str = num_str.replace(" ", "")

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


def parse_area(raw: Optional[str]) -> Optional[float]:
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

def parse_int(raw: Optional[str]) -> Optional[int]:
    """Parse integer from string, handling 'T3' → 3 for typology."""
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    if match:
        return int(match.group())
    return None


# ───────── Boolean Mapping ─────────

def parse_bool(raw: Optional[str]) -> Optional[bool]:
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


def parse_date(raw: Optional[str]) -> Optional[datetime]:
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

def typology_to_bedrooms(typology: Optional[str]) -> Optional[int]:
    """Extract bedrooms from typology string: 'T3' → 3, 'T0' → 0."""
    if not typology:
        return None
    match = re.search(r"[Tt](\d+)", typology)
    if match:
        return int(match.group(1))
    return None


# ───────── Price per m² Calculation ─────────

def calculate_price_per_m2(
    price_amount: Optional[Decimal],
    area: Optional[float],
) -> Optional[Decimal]:
    """Calculate price per m² from price and area."""
    if price_amount and area and area > 0:
        return Decimal(str(round(float(price_amount) / area, 2)))
    return None


# ───────── Partner Normalizers ─────────

def normalize_pearls_payload(raw: Dict[str, Any]) -> PropertySchema:
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
            "raw": raw.get("raw_description", ""),
        },
        energy_certificate=raw.get("energy_certificate"),
        construction_year=parse_int(raw.get("construction_year")),
        advertiser=raw.get("advertiser"),
        contacts=raw.get("contacts"),
        raw_partner_payload=raw,
    )

# ───────── Dispatcher ─────────

_PARTNER_NORMALIZERS = {
    "pearls": normalize_pearls_payload,
}


def normalize_partner_payload(raw: Dict[str, Any], partner: str) -> PropertySchema:
    """Dispatch normalization to the appropriate partner normalizer."""
    normalizer = _PARTNER_NORMALIZERS.get(partner)
    if not normalizer:
        raise ValueError(f"No normalizer registered for partner: '{partner}'")
    return normalizer(raw)


# ───────── PropertySchema → DB model fields ─────────

def schema_to_listing_dict(schema: PropertySchema, scrape_job_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Convert a canonical PropertySchema to a dict suitable for creating a Listing ORM model."""
    return {
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
        "description_quality_score": schema.description_quality_score,
        "page_title": schema.seo.get("page_title") if schema.seo else None,
        "headers": schema.seo.get("headers") if schema.seo else None,
        "meta_description": schema.seo.get("meta_description") if schema.seo else None,
        "raw_payload": schema.raw_partner_payload,
        "scrape_job_id": scrape_job_id,
    }
