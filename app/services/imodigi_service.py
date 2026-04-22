"""Imodigi CRM service — push listings to the Imodigi API.

Integration reference: API_IMODIGI_V1

Authentication:  X-API-Token header.
Base URL:        settings.imodigi_base_url  (default: https://imodigi.com/crm_api)
Client ID:       settings.imodigi_client_id — required for all property operations.

External API endpoints used:
  GET  /crm-stores.php              → list stores
  GET  /crm-property-values.php     → allowed catalog values
  GET  /crm-locations.php           → location hierarchy search
  POST /crm-properties.php          → create property (returns {property, reference})
  PATCH /crm-properties.php         → update existing property
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.imodigi_adapter import imodigi_adapter
from app.config import settings
from app.core.exceptions import ImodigiError, NotFoundError
from app.core.logging import get_logger
from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing
from app.repositories.imodigi_repository import ImodigiRepository

logger = get_logger(__name__)

# ─────────────────────────── Type mappings ──────────────────────────────

_LISTING_TYPE_MAP: dict[str, str] = {
    "sale": "To Buy",
    "rent": "To Rent",
}

_PROPERTY_TYPE_MAP: dict[str, str] = {
    "apartment": "Apartment",
    "apartamento": "Apartment",
    "house": "House",
    "moradia": "House",
    "moradia geminada": "House",
    "land": "Land",
    "terreno": "Land",
    "commercial": "Commercial",
    "loja": "Commercial",
    "escritório": "Commercial",
    "office": "Office",
    "garage": "Garage",
    "garagem": "Garage",
}

_CONDITION_MAP: dict[str, str] = {
    "new": "New",
    "novo": "New",
    "used": "Used",
    "usado": "Used",
    "renovated": "Renovated",
    "renovado": "Renovated",
}


def _map_property_type(raw: str | None) -> str | None:
    if not raw:
        return None
    return _PROPERTY_TYPE_MAP.get(raw.lower().strip(), raw)


def _map_condition(raw: str | None) -> str | None:
    if not raw:
        return None
    return _CONDITION_MAP.get(raw.lower().strip())


def build_property_payload(listing: Listing) -> dict[str, Any]:
    """Convert a Listing ORM instance to the Imodigi property payload dict.

    Only non-None fields are included so partial PATCH calls stay minimal.
    """
    business_type = _LISTING_TYPE_MAP.get(listing.listing_type or "", "To Buy")
    property_type = _map_property_type(listing.property_type)

    payload: dict[str, Any] = {
        "businessType": business_type,
        "availability": "Available",
        "isActive": True,
    }

    if listing.partner_id:
        payload["reference"] = listing.partner_id
    if listing.title:
        payload["title"] = listing.title
    if listing.description:
        payload["description"] = listing.description
    if listing.meta_description:
        payload["shortDescription"] = listing.meta_description
    if property_type:
        payload["propertyType"] = property_type

    # Location
    location: dict[str, Any] = {"country": "Portugal"}
    if listing.district:
        location["district"] = listing.district
    if listing.county:
        location["county"] = listing.county
    if listing.parish:
        location["parish"] = listing.parish
    payload["location"] = location

    # Pricing
    if listing.price_amount is not None:
        payload["pricing"] = {"price": float(listing.price_amount), "publishPrice": True}

    # Coordinates
    if listing.latitude is not None and listing.longitude is not None:
        payload["coordinates"] = {
            "lat": str(listing.latitude),
            "lng": str(listing.longitude),
            "publish": True,
        }

    # Areas
    areas: dict[str, Any] = {}
    if listing.area_useful_m2 is not None:
        areas["useful"] = listing.area_useful_m2
    if listing.area_gross_m2 is not None:
        areas["gross"] = listing.area_gross_m2
    if listing.area_land_m2 is not None:
        areas["land"] = listing.area_land_m2
    if areas:
        payload["areas"] = areas

    # Rooms
    rooms: dict[str, Any] = {}
    if listing.bedrooms is not None:
        rooms["bedrooms"] = listing.bedrooms
    if listing.bathrooms is not None:
        rooms["bathrooms"] = listing.bathrooms
    if rooms:
        payload["rooms"] = rooms

    # Energy
    if listing.energy_certificate:
        payload["energy"] = {"class": listing.energy_certificate}

    # Images — limit to 20 per imodigi constraint
    if listing.media_assets:
        payload["images"] = [a.url for a in listing.media_assets if a.url][:20]

    # Translations — built from enriched_translations (all locales), EN falls back to canonical fields
    translations: dict[str, dict[str, str]] = {}
    enriched: dict[str, Any] = listing.enriched_translations or {}

    for locale, locale_data in enriched.items():
        if not isinstance(locale_data, dict):
            continue
        entry: dict[str, str] = {}
        if locale_data.get("title"):
            entry["title"] = locale_data["title"]
        if locale_data.get("description"):
            entry["description"] = locale_data["description"]
        if locale_data.get("meta_description"):
            entry["shortDescription"] = locale_data["meta_description"]
        if entry:
            translations[locale] = entry

    if "en" not in translations:
        entry_en: dict[str, str] = {}
        if listing.title:
            entry_en["title"] = listing.title
        if listing.description:
            entry_en["description"] = listing.description
        if listing.meta_description:
            entry_en["shortDescription"] = listing.meta_description
        if entry_en:
            translations["en"] = entry_en

    if translations:
        payload["translations"] = translations
    logger.debug("Imodigi payload built", extra={"listing_id": str(listing.id)})
    return payload


# ─────────────────────────── API client calls ───────────────────────────

async def get_stores() -> list[dict[str, Any]]:
    """GET /crm-stores.php — return list of active stores."""
    return await imodigi_adapter.get_stores()


async def get_catalog_values() -> dict[str, Any]:
    """GET /crm-property-values.php — return allowed catalog values."""
    return await imodigi_adapter.get_catalog_values()


async def search_locations(
    level: str,
    *,
    country_id: int | None = None,
    region_id: int | None = None,
    district_id: int | None = None,
    county_id: int | None = None,
    q: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """GET /crm-locations.php — search locations by hierarchical level."""
    return await imodigi_adapter.search_locations(
        level,
        country_id=country_id,
        region_id=region_id,
        district_id=district_id,
        county_id=county_id,
        q=q,
        limit=limit,
    )


async def create_property(
    client_id: int,
    property_payload: dict[str, Any],
    *,
    images: list[str] | None = None,
    translations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /crm-properties.php — create a new property. Returns full response body."""
    return await imodigi_adapter.create_property(client_id, property_payload, images=images, translations=translations)


async def update_property(
    client_id: int,
    imodigi_property_id: int,
    property_payload: dict[str, Any],
    *,
    images: list[str] | None = None,
    translations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PATCH /crm-properties.php — update an existing property."""
    return await imodigi_adapter.update_property(client_id, imodigi_property_id, property_payload, images=images, translations=translations)


async def export_listing(
    listing: Listing,
    *,
    client_id: int,
    existing_imodigi_id: int | None,
) -> tuple[int | None, str | None, str]:
    """Export a single listing to Imodigi.

    Returns (imodigi_property_id, imodigi_reference, action)
    where action is 'created' or 'updated'.
    """
    payload = build_property_payload(listing)
    # Extract top-level fields before sending property payload
    images: list[str] | None = payload.pop("images", None)
    translations: dict[str, Any] | None = payload.pop("translations", None)

    if existing_imodigi_id is None:
        logger.info("Creating new imodigi property for listing %s", listing.id)
        result = await create_property(client_id, payload, images=images, translations=translations)
        return result.get("property"), result.get("reference"), "created"

    logger.info("Updating imodigi property %d for listing %s", existing_imodigi_id, listing.id)
    await update_property(client_id, existing_imodigi_id, payload, images=images, translations=translations)
    return existing_imodigi_id, None, "updated"


# ─────────────────────────── Higher-level orchestration ─────────────────────


async def export_listing_to_crm(
    db: AsyncSession,
    listing_id: UUID,
    client_id: int,
) -> tuple[ImodigiExport, str]:
    """Full export workflow: fetch listing, call Imodigi API, persist record.

    Returns (export_record, action) where action is 'created' or 'updated'.
    Raises NotFoundError if the listing does not exist.
    On ImodigiError, persists the failure record before re-raising.
    """
    listing = (
        await db.execute(
            select(Listing)
            .where(Listing.id == listing_id)
            .options(selectinload(Listing.media_assets))
        )
    ).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    existing = await ImodigiRepository.get_export_by_listing_id(db, listing_id)
    existing_imodigi_id = existing.imodigi_property_id if existing else None

    try:
        imodigi_id, imodigi_ref, action = await export_listing(
            listing,
            client_id=client_id,
            existing_imodigi_id=existing_imodigi_id,
        )
        status = "published" if action == "created" else "updated"
        export_record = await ImodigiRepository.upsert_export(
            db,
            listing_id=listing_id,
            imodigi_property_id=imodigi_id,
            imodigi_reference=imodigi_ref or (existing.imodigi_reference if existing else None),
            imodigi_client_id=client_id,
            status=status,
            last_error=None,
        )
        await db.commit()
        await db.refresh(export_record)
        return export_record, action
    except ImodigiError as exc:
        await ImodigiRepository.upsert_export(
            db,
            listing_id=listing_id,
            imodigi_property_id=existing_imodigi_id,
            imodigi_reference=existing.imodigi_reference if existing else None,
            imodigi_client_id=client_id,
            status="failed",
            last_error=str(exc),
        )
        await db.commit()
        raise


async def list_export_records(
    db: AsyncSession,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[ImodigiExport], int]:
    """List Imodigi export records with optional status filter."""
    return await ImodigiRepository.list_exports(db, status=status, page=page, page_size=page_size)


async def get_export_record(
    db: AsyncSession,
    listing_id: UUID,
) -> ImodigiExport:
    """Get the Imodigi export record for a listing. Raises NotFoundError if absent."""
    record = await ImodigiRepository.get_export_by_listing_id(db, listing_id)
    if not record:
        raise NotFoundError(f"No Imodigi export found for listing {listing_id}")
    return record


async def reset_export_record(
    db: AsyncSession,
    listing_id: UUID,
) -> None:
    """Delete the Imodigi export record for a single listing.

    After reset, the next export will send a POST (create) instead of PATCH (update).
    Raises NotFoundError if no export record exists.
    """
    deleted = await ImodigiRepository.delete_export_by_listing_id(db, listing_id)
    if not deleted:
        raise NotFoundError(f"No Imodigi export found for listing {listing_id}")
    await db.commit()


async def reset_export_records(
    db: AsyncSession,
    listing_ids: list[UUID],
) -> int:
    """Delete Imodigi export records for the given listings, or ALL if the list is empty.

    Returns the number of records deleted.
    After reset, the next export will send a POST (create) instead of PATCH (update).
    """
    if listing_ids:
        count = await ImodigiRepository.delete_exports_by_listing_ids(db, listing_ids)
    else:
        count = await ImodigiRepository.delete_all_exports(db)
    await db.commit()
    return count


async def get_listing_ids_for_bulk_imodigi(
    db: AsyncSession,
    listing_ids: list[UUID],
    limit: int,
) -> list[UUID]:
    """Return listing IDs to process for bulk Imodigi export.

    When *listing_ids* is provided, use those. Otherwise, return all listings that
    have never been published or whose last export failed (up to *limit*).
    """
    from sqlalchemy import not_

    if listing_ids:
        return list(listing_ids)

    # Subquery: listing IDs that are already published/updated
    published_subq = (
        select(ImodigiExport.listing_id)
        .where(ImodigiExport.status.in_(["published", "updated"]))
        .scalar_subquery()
    )
    stmt = (
        select(Listing.id)
        .where(not_(Listing.id.in_(published_subq)))
        .order_by(Listing.created_at.asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Background runner — called via FastAPI BackgroundTasks
# ---------------------------------------------------------------------------


async def run_bulk_imodigi_job(job_id: UUID, listing_ids: list[UUID], client_id: int) -> None:
    """Background task: export listings to Imodigi and update job progress in-store."""
    from datetime import datetime, timezone

    from app.database import async_session_factory
    from app.services.bulk_job_store import STATUS_COMPLETED, STATUS_FAILED, get_job

    job = get_job(job_id)
    if job is None:
        logger.error("run_bulk_imodigi_job: job %s not found in store", job_id)
        return

    done = 0
    failed = 0
    results = []

    for lid in listing_ids:
        try:
            async with async_session_factory() as db:
                export_record, action = await export_listing_to_crm(db, lid, client_id)
            job.done += 1
            done += 1
            results.append({
                "listing_id": str(lid),
                "status": export_record.status,
                "action": action,
                "imodigi_property_id": export_record.imodigi_property_id,
            })
        except Exception as exc:
            logger.warning("Bulk Imodigi export failed for listing %s: %s", lid, exc)
            job.failed += 1
            failed += 1
            job.errors.append(f"{lid}: {exc}")
            results.append({"listing_id": str(lid), "status": "failed", "error": str(exc)})

    job.result = {"total": len(listing_ids), "done": done, "failed": failed, "results": results}
    job.status = STATUS_FAILED if done == 0 and failed > 0 else STATUS_COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    logger.info(
        "Bulk Imodigi export job %s finished: done=%s failed=%s",
        job_id,
        done,
        failed,
    )
