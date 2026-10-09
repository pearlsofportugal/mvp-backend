"""Stable content hash for scraped listings.

``updated_at`` is a promise to API consumers (the WordPress sync re-imports a
listing only when it moves), so it must only move when something a consumer can
see actually changed. The hash is computed over normalised *content* fields and
the ordered gallery URLs, and deliberately ignores crawl bookkeeping
(``scrape_job_id``, ``last_seen_at``), raw payloads and scraped SEO chrome.
"""
import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Order is irrelevant (keys are sorted when serialised) but the set is part of
# the hash contract: adding a field here changes every hash exactly once.
CONTENT_FIELDS: tuple[str, ...] = (
    "title", "business_type", "property_type", "condition", "typology", "typology_extra",
    "bedrooms", "bathrooms", "floor",
    "price_amount", "price_currency", "price_per_m2", "price_on_request",
    "area_useful_m2", "area_gross_m2", "area_land_m2",
    "district", "county", "parish", "full_address", "latitude", "longitude",
    "has_garage", "has_elevator", "has_balcony", "has_air_conditioning", "has_pool", "has_garden",
    "energy_certificate", "construction_year", "advertiser", "contacts",
    "raw_description", "description", "description_clean", "description_quality_score", "meta_description",
)

_CENT = Decimal("0.01")


def _norm(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))
    if isinstance(value, float):
        # Float columns round-trip exactly, but scraped values can carry noise
        # (80.0 vs 80.00000000000001) — 6 decimals is far below any real change.
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, str):
        collapsed = " ".join(value.split())
        return collapsed or None
    return value


def compute_content_hash(values: Mapping[str, Any], media_urls: Sequence[str] = ()) -> str:
    """SHA-256 over the normalised content fields in ``values`` plus ordered media URLs."""
    payload = {field: _norm(values.get(field)) for field in CONTENT_FIELDS}
    payload["media"] = [str(u) for u in media_urls]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
