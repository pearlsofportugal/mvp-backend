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
  - Cache loaded at startup via init_mapper_cache() (chamada no lifespan do main.py)
  - Cache também é refrescado lazily com TTL em parse_price()

CORREÇÕES v2:
  - asyncio.Lock protege a recarga do cache contra race conditions
  - Double-check pattern após adquirir o lock (evita dupla carga)
  - _CACHE_TIMESTAMP atualizado mesmo em fallback (evita retry loop se DB estiver em baixo)

Expandable per partner — dispatcher pattern.
"""
import asyncio
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




_CURRENCY_MAP_CACHE: Dict[str, str] = {}
_CACHE_TIMESTAMP: Optional[datetime] = None
_CACHE_TTL_SECONDS = 300  


_cache_lock = asyncio.Lock()

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


async def _load_currency_map() -> None:
    """Carrega currency mappings da DB com caching e proteção contra race conditions.

    Usa asyncio.Lock com double-check pattern para garantir que apenas um coroutine
    recarrega o cache de cada vez — essencial quando múltiplos jobs correm concorrentemente.
    """
    global _CURRENCY_MAP_CACHE, _CACHE_TIMESTAMP

    now = datetime.now(timezone.utc)

    if (
        _CACHE_TIMESTAMP
        and _CURRENCY_MAP_CACHE
        and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return

    async with _cache_lock:
        now = datetime.now(timezone.utc)
        if (
            _CACHE_TIMESTAMP
            and _CURRENCY_MAP_CACHE
            and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
        ):
            return

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
                    extended_map: Dict[str, str] = {}
                    for k, v in currency_map.items():
                        extended_map[k] = v
                        extended_map[k.lower()] = v

                    _CURRENCY_MAP_CACHE = extended_map
                    _CACHE_TIMESTAMP = now
                    logger.debug("Loaded %d currency mappings from DB", len(currency_map))
                    return

        except Exception as e:
            logger.warning("Could not load currency map from DB: %s. Using defaults.", str(e))


        _CURRENCY_MAP_CACHE = _DEFAULT_CURRENCY_MAP.copy()
        _CACHE_TIMESTAMP = now


def _get_currency_map() -> Dict[str, str]:
    """Retorna o currency map em cache (sync). Usa defaults se o cache estiver vazio."""
    if _CURRENCY_MAP_CACHE:
        return _CURRENCY_MAP_CACHE
    return _DEFAULT_CURRENCY_MAP


def invalidate_mapper_cache() -> None:
    """Limpa o cache do mapper (chamar após atualizações de config na DB)."""
    global _CURRENCY_MAP_CACHE, _CACHE_TIMESTAMP
    _CURRENCY_MAP_CACHE = {}
    _CACHE_TIMESTAMP = None
    logger.info("Mapper configuration cache invalidated")


async def init_mapper_cache() -> None:
    """Inicializa o cache do mapper carregando os currency mappings da DB.

    Deve ser chamada no startup da aplicação (lifespan) para garantir que o
    cache está pronto antes do primeiro job de scraping arrancar.
    """
    await _load_currency_map()



_PRICE_PATTERN = re.compile(r"[\d\s.,]+")


def parse_price(raw: Optional[str]) -> Tuple[Optional[Decimal], Optional[str]]:
    """Parse a price string like '250 000 €' into (Decimal(250000), 'EUR')."""
    if not raw:
        return None, None

    match = _PRICE_PATTERN.search(raw)
    if not match:
        return None, None

    num_str = match.group().strip()

    num_str = num_str.replace(" ", "")


    if "," in num_str and "." in num_str:
        num_str = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts[-1]) == 2:
            num_str = num_str.replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    elif "." in num_str:
        parts = num_str.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            num_str = num_str.replace(".", "")

    try:
        amount = Decimal(num_str)
    except Exception:
        logger.warning("Failed to parse price amount from: '%s'", raw)
        return None, None

    currency_map = _get_currency_map()
    currency = "EUR"  
    raw_lower = raw.lower()
    for symbol, code in currency_map.items():
        if symbol in raw_lower or symbol in raw:
            currency = code
            break

    return amount, currency



_AREA_PATTERN = re.compile(r"([\d\s.,]+)\s*m[²2]?", re.IGNORECASE)


def parse_area(raw: Optional[str]) -> Optional[float]:
    """Parse an area string like '120 m²' into 120.0."""
    if not raw:
        return None

    match = _AREA_PATTERN.search(raw)
    if not match:
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



def parse_int(raw: Optional[str]) -> Optional[int]:
    """Parse integer from string, handling 'T3' → 3 for typology."""
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    if match:
        return int(match.group())
    return None



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



def typology_to_bedrooms(typology: Optional[str]) -> Optional[int]:
    """Extract bedrooms from typology string: 'T3' → 3, 'T0' → 0."""
    if not typology:
        return None
    match = re.search(r"[Tt](\d+)", typology)
    if match:
        return int(match.group(1))
    return None



def calculate_price_per_m2(
    price_amount: Optional[Decimal],
    area: Optional[float],
) -> Optional[Decimal]:
    """Calculate price per m² from price and area."""
    if price_amount and area and area > 0:
        return Decimal(str(round(float(price_amount) / area, 2)))
    return None



def normalize_pearls_payload(raw: Dict[str, Any]) -> PropertySchema:
    """Normalize a raw Pearls of Portugal payload into canonical PropertySchema."""
    price_amount, price_currency = parse_price(raw.get("price"))
    useful_area = parse_area(raw.get("useful_area"))
    gross_area = parse_area(raw.get("gross_area"))
    land_area = parse_area(raw.get("land_area"))

    bedrooms = parse_int(raw.get("bedrooms"))
    if bedrooms is None:
        bedrooms = typology_to_bedrooms(raw.get("typology"))
    
    business_type = (raw.get("business_type") or "").lower().strip()
    listing_type = "sale"  
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


_PARTNER_NORMALIZERS = {
    "pearls": normalize_pearls_payload,
}


def normalize_partner_payload(raw: Dict[str, Any], partner: str) -> PropertySchema:
    """Dispatch normalization to the appropriate partner normalizer."""
    normalizer = _PARTNER_NORMALIZERS.get(partner)
    if not normalizer:
        raise ValueError(f"No normalizer registered for partner: '{partner}'")
    return normalizer(raw)



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
