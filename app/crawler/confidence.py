"""Confidence scoring for post-crawl field extraction coverage."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_SCORABLE_FIELDS = (
    "price",
    "title",
    "area",
    "rooms",
    "location",
    "images",
    # Extended fields
    "property_type",
    "typology",
    "condition",
    "business_type",
    "land_area",
)

# Validators mirror those in selector_suggester to score quality, not just presence
_PRICE_RE = re.compile(r"\d.*(?:€|eur|euro)", re.IGNORECASE)
_AREA_RE = re.compile(r"\d+(?:[\.,]\d+)?\s*(?:m2|m²|metros?)", re.IGNORECASE)
_TYPOLOGY_RE = re.compile(r"\bT\d+(?:\+\d+)?\b", re.IGNORECASE)
_CONDITION_RE = re.compile(r"\b(?:usado|novo|renovado|recuperado|excelente|bom\s+estado|em\s+construcao|na\s+planta|para\s+recuperar)\b", re.IGNORECASE)
_BUSINESS_RE = re.compile(r"\b(?:venda|comprar|arrendar|arrendamento|sale|rent|buy)\b", re.IGNORECASE)
_PROPERTY_TYPE_RE = re.compile(r"\b(?:moradia|apartamento|terreno|loja|armaz[eé]m|escrit[oó]rio|garagem|quintinha|quinta|vivenda)\b", re.IGNORECASE)


def calculate_confidence(results: list[Any]) -> dict[str, float]:
    """Return field-level extraction coverage for a completed crawl."""
    total = len(results)
    if total == 0:
        return {field: 0.0 for field in _SCORABLE_FIELDS}

    presence_counts = {field: 0 for field in _SCORABLE_FIELDS}
    for result in results:
        if _has_price(result):
            presence_counts["price"] += 1
        if _has_title(result):
            presence_counts["title"] += 1
        if _has_area(result):
            presence_counts["area"] += 1
        if _has_rooms(result):
            presence_counts["rooms"] += 1
        if _has_location(result):
            presence_counts["location"] += 1
        if _has_images(result):
            presence_counts["images"] += 1
        if _has_valid_field(result, "property_type", _PROPERTY_TYPE_RE):
            presence_counts["property_type"] += 1
        if _has_valid_field(result, "typology", _TYPOLOGY_RE):
            presence_counts["typology"] += 1
        if _has_valid_field(result, "condition", _CONDITION_RE):
            presence_counts["condition"] += 1
        if _has_valid_field(result, "business_type", _BUSINESS_RE):
            presence_counts["business_type"] += 1
        if _has_land_area(result):
            presence_counts["land_area"] += 1

    return {
        field: round(presence_counts[field] / total, 2)
        for field in _SCORABLE_FIELDS
    }


def log_low_confidence_scores(site_key: str, scores: dict[str, float], threshold: float = 0.7) -> None:
    """Emit warnings for fields that fell below the desired extraction threshold."""
    for field, score in scores.items():
        if score < threshold:
            logger.warning(
                "Low extraction confidence for site '%s' field '%s': %.2f",
                site_key,
                field,
                score,
                extra={"site_key": site_key},
            )


def _has_price(result: Any) -> bool:
    if _attr(result, "price_amount") is not None:
        return True
    price = _attr(result, "price")
    if price is None:
        return False
    return _attr(price, "amount") is not None or _mapping_get(price, "amount") is not None


def _has_title(result: Any) -> bool:
    return _present(_attr(result, "title"))


def _has_area(result: Any) -> bool:
    values = (
        _attr(result, "area_useful_m2"),
        _attr(result, "area_gross_m2"),
        _attr(result, "area_land_m2"),
    )
    return any(value is not None for value in values)


def _has_rooms(result: Any) -> bool:
    return _attr(result, "bedrooms") is not None or _attr(result, "bathrooms") is not None


def _has_location(result: Any) -> bool:
    direct_values = (
        _attr(result, "full_address"),
        _attr(result, "district"),
        _attr(result, "county"),
        _attr(result, "parish"),
    )
    if any(_present(value) for value in direct_values):
        return True

    address = _attr(result, "address")
    if address is None:
        return False

    nested_values = (
        _attr(address, "full_address") or _mapping_get(address, "full_address"),
        _attr(address, "region") or _mapping_get(address, "region"),
        _attr(address, "city") or _mapping_get(address, "city"),
        _attr(address, "area") or _mapping_get(address, "area"),
    )
    return any(_present(value) for value in nested_values)


def _has_images(result: Any) -> bool:
    media = _attr(result, "media_assets")
    if isinstance(media, Sequence) and not isinstance(media, (str, bytes)):
        return len(media) > 0

    media = _attr(result, "media")
    if isinstance(media, Sequence) and not isinstance(media, (str, bytes)):
        return len(media) > 0

    return False


def _has_valid_field(result: Any, field: str, pattern: re.Pattern) -> bool:
    """Return True only when the field value passes a quality regex check."""
    value = _attr(result, field)
    if not _present(value):
        return False
    return bool(pattern.search(str(value)))


def _has_land_area(result: Any) -> bool:
    value = _attr(result, "area_land_m2")
    if value is not None:
        return True
    value = _attr(result, "land_area")
    return _present(value) and bool(_AREA_RE.search(str(value)))


def _attr(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def _mapping_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
