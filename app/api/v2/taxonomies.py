"""Controlled dictionaries the plugin turns into Houzez terms (property types, features, energy, places...)."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.v2.listings import partner_profiles
from app.contract_v2 import SCHEMA_VERSION
from app.contract_v2.features import FEATURE_LABELS
from app.contract_v2.mappings import (
    BUSINESS_TYPES,
    CONDITION_LABELS,
    ENERGY_LABELS,
    PROPERTY_TYPE_LABELS,
    STATUSES,
    SUBTYPE_LABELS,
    SUBTYPES_BY_TYPE,
)
from app.contract_v2.text import slugify
from app.services.geo_normalizer import get_geo_index

router = APIRouter()


def _entries(labels: dict[str, tuple[str, str]]) -> list[dict]:
    return [{"code": code, "label_pt": pt, "label_en": en} for code, (pt, en) in labels.items()]


@router.get("", summary="Controlled dictionaries of the contract")
async def taxonomies(db: AsyncSession = Depends(get_db)) -> dict:
    partners = await partner_profiles(db)
    return {
        "schema_version": SCHEMA_VERSION,
        "business_types": _entries(BUSINESS_TYPES),
        "property_types": [
            {
                "code": code,
                "label_pt": pt,
                "label_en": en,
                "subtypes": [
                    {"code": s, "label_pt": SUBTYPE_LABELS[s][0], "label_en": SUBTYPE_LABELS[s][1]}
                    for s in SUBTYPES_BY_TYPE.get(code, ())
                ],
            }
            for code, (pt, en) in PROPERTY_TYPE_LABELS.items()
        ],
        "conditions": _entries(CONDITION_LABELS),
        "energy_classes": _entries(ENERGY_LABELS),
        "features": _entries(FEATURE_LABELS),
        "statuses": list(STATUSES),
        "partners": [{"key": key, "name": profile["name"]} for key, profile in sorted(partners.items())],
    }


@router.get("/places", summary="Official districts, counties and parishes (DICOFRE)")
async def places(
    level: Literal["district", "county", "parish"] = Query(...),
    parent: str | None = Query(None, min_length=2, max_length=4, pattern=r"^\d+$", description="Parent code: a district (2 digits) or a county (4 digits)."),
) -> dict:
    geo = get_geo_index()
    rows = geo.places(level, parent) if geo else []
    return {
        "schema_version": SCHEMA_VERSION,
        "level": level,
        "parent": parent,
        "items": [{"code": code, "name": name, "slug": slugify(name)} for code, name in rows],
    }
