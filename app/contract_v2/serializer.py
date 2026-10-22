"""Listing -> contract v2 object (see app/contracts/listing-2.0.schema.json).

`to_contract` is a pure function of an ORM listing (with `media_assets` and `price_history`
loaded) and the partner profile. It never raises for incomplete data: gaps become `null`
or entries in `quality.issues`, as the contract asks, so a bad record cannot break the plugin.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.contract_v2.features import extract_features
from app.contract_v2.mappings import (
    PROPERTY_TYPE_LABELS,
    condition_code,
    energy_class,
    property_type_to_contract,
    typology_to_contract,
)
from app.contract_v2.media import serialize_media
from app.contract_v2.quality import assess
from app.contract_v2.text import (
    DESCRIPTION_MIN,
    clean_text,
    has_contact,
    make_description,
    make_meta_description,
    make_title,
    slugify,
    strip_contacts,
)
from app.services.geo_normalizer import get_geo_index

SCHEMA_VERSION = "2.0"
LANGUAGES = ("pt", "en", "es", "fr", "de")
UNKNOWN_PLACE = "Desconhecido"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _num(value: Any) -> int | float | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number == int(number) else round(number, 2)


def _in_range(value: int | None, low: int, high: int) -> int | None:
    return value if value is not None and low <= value <= high else None


def _place(code: str | None, raw_name: str | None) -> dict | None:
    """{code, name, slug} with the official name when the code is known, else the text we scraped."""
    geo = get_geo_index()
    official = geo.name_for_code(code) if geo and code else None
    name = official or (raw_name or "").strip() or None
    if not name:
        return None
    return {"code": code, "name": name, "slug": slugify(name)}


def _price(listing) -> dict:
    amount = listing.price_amount
    on_request = bool(getattr(listing, "price_on_request", False)) or (amount is not None and float(amount) < 0)
    current = None if on_request or amount is None else _num(amount)
    previous = None
    history = sorted(getattr(listing, "price_history", None) or [], key=lambda h: h.recorded_at, reverse=True)
    for entry in history:
        if current is not None and _num(entry.price_amount) != current:
            previous = _num(entry.price_amount)
            break
    return {
        "amount": current,
        "currency": "EUR",
        "period": "month" if listing.business_type == "rent" else None,
        "on_request": on_request,
        "negotiable": None,
        "previous_amount": previous,
        "per_m2": _num(listing.price_per_m2),
    }


def _translation(title: str | None, description: str | None, meta: str | None, highlights, fallback_title: str) -> dict | None:
    """A valid language entry or None when the text is too short to publish."""
    text = strip_contacts(clean_text(description))
    if len(text) < DESCRIPTION_MIN:
        return None
    entry: dict[str, Any] = {
        "title": make_title(title, fallback_title),
        "description": text,
        "meta_description": make_meta_description(meta),
    }
    if isinstance(highlights, list):
        entry["highlights"] = [str(h) for h in highlights if str(h).strip()][:10]
    return entry


def to_contract(listing, partner: dict | None = None) -> dict:
    """The v2 object for *listing*; *partner* is the profile of its source (key, name, logo_url, ...)."""
    ptype = property_type_to_contract(listing.property_type, listing.title)
    typology = typology_to_contract(listing.typology, listing.typology_extra)

    district = _place(listing.district_code, listing.district)
    county = _place(listing.county_code, listing.county)
    parish = _place(listing.parish_code, listing.parish)
    place_ok = district is not None and county is not None
    location = {
        "district": district or {"code": None, "name": (county or {}).get("name") or UNKNOWN_PLACE, "slug": None},
        "county": county or {"code": None, "name": (district or {}).get("name") or UNKNOWN_PLACE, "slug": None},
        "parish": parish,
        "street": None,
        "postal_code": None,
        "display_address": ", ".join(p["name"] for p in (parish, county) if p) or (district or {}).get("name"),
    }

    # synthesised text for the cases where the source gave us too little
    type_label_pt = PROPERTY_TYPE_LABELS[ptype["code"]][0]
    where = (parish or county or district or {}).get("name")
    fallback_title = " ".join(p for p in (type_label_pt, typology["label"] if typology else None) if p) + (f" em {where}" if where else "")
    sentence = f"{fallback_title}."
    if listing.area_useful_m2:
        sentence += f" Área útil de {_num(listing.area_useful_m2)} m²."
    elif listing.area_gross_m2:
        sentence += f" Área bruta de {_num(listing.area_gross_m2)} m²."

    source_text = listing.description_clean or listing.description or listing.raw_description or ""
    had_contact = has_contact(source_text) or has_contact(listing.title)

    translations: dict[str, dict] = {}
    enriched = listing.enriched_translations if isinstance(listing.enriched_translations, dict) else {}
    for lang in LANGUAGES:
        entry = enriched.get(lang)
        if isinstance(entry, dict):
            valid = _translation(entry.get("title"), entry.get("description"), entry.get("meta_description"), entry.get("highlights"), fallback_title)
            if valid:
                translations[lang] = valid
    is_enriched = bool(translations)

    description_synthesised = False
    if "pt" not in translations:
        text, description_synthesised = make_description(source_text, sentence)
        translations["pt"] = {
            "title": make_title(listing.title, fallback_title),
            "description": text,
            "meta_description": make_meta_description(listing.meta_description),
        }
    translations = {lang: translations[lang] for lang in LANGUAGES if lang in translations}

    features = extract_features(listing, f"{listing.title or ''} {source_text}")
    media = serialize_media(getattr(listing, "media_assets", None))
    price = _price(listing)
    removed = listing.status == "removed"

    quality = assess(
        photo_count=sum(1 for m in media if m["type"] == "photo"),
        widths=[m["width"] for m in media if m["type"] == "photo" and m["width"]],
        watermarked=any(m["has_watermark"] for m in media),
        price_ok=price["amount"] is not None or price["on_request"],
        area_ok=any((listing.area_useful_m2, listing.area_gross_m2, listing.area_land_m2)),
        description_synthesised=description_synthesised,
        had_contact=had_contact,
        address_matched=bool(listing.district_code and listing.county_code),
        features=features,
        place_ok=place_ok,
        active=not removed,
    )

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": str(listing.id),
        "external_ref": listing.partner_id,
        "canonical_id": None,
        "duplicate_group_id": None,
        "status": "removed" if removed else "active",
        "removed_at": _iso(listing.removed_at) if removed else None,
        "removed_reason": "not_found" if removed else None,
        "first_seen_at": _iso(listing.first_seen_at or listing.created_at),
        "last_seen_at": _iso(listing.last_seen_at or listing.updated_at),
        "published_at": None,
        "updated_at": _iso(listing.updated_at),
        "content_hash": f"sha256:{listing.content_hash[:16]}" if listing.content_hash else None,
        "source": {
            "partner_key": listing.source_partner,
            "partner_id": listing.partner_id,
            "url": listing.source_url,
            "is_exported_to_imodigi": None,
        },
        "partner": partner,
        "business_type": listing.business_type or "sale",
        "property_type": ptype,
        "condition": condition_code(listing.condition),
        "typology": typology,
        "location": location,
        "price": price,
        "areas": {
            "useful_m2": _num(listing.area_useful_m2),
            "gross_m2": _num(listing.area_gross_m2),
            "land_m2": _num(listing.area_land_m2),
            "terrace_m2": None,
        },
        "rooms": {
            "bedrooms": _in_range(listing.bedrooms, 0, 50),
            "bathrooms": _in_range(listing.bathrooms, 0, 50),
            "rooms_total": None,
            "parking_spaces": None,
            "floor": (listing.floor or None) and str(listing.floor)[:20],
            "floors_total": None,
        },
        "building": {
            "construction_year": _in_range(listing.construction_year, 1000, 2100),
            "energy_class": energy_class(listing.energy_certificate),
            "energy_certificate_ref": None,
        },
        "features": features,
        "content": {
            "default_language": "pt",
            "is_enriched": is_enriched,
            "quality_score": listing.description_quality_score,
            "translations": translations,
        },
        "media": media,
        "quality": quality,
    }
    return result


def partner_profile(site) -> dict | None:
    """The `partner` object from a SiteConfig-like row (None when the listing has no known source)."""
    if site is None:
        return None
    from urllib.parse import urlparse

    origin = urlparse(site.base_url or "")
    website = getattr(site, "website", None) or (f"{origin.scheme}://{origin.netloc}" if origin.netloc else None)
    return {
        "key": site.key,
        "name": site.name,
        "legal_name": getattr(site, "legal_name", None),
        "ami_licence": getattr(site, "ami_licence", None),
        "logo_url": getattr(site, "logo_url", None),
        "website": website,
        "phone": getattr(site, "phone", None),
        "email": getattr(site, "email", None),
        "address": getattr(site, "address", None),
    }
