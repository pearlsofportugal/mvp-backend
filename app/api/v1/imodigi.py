"""Imodigi CRM API router — publish listings to the Imodigi platform.
/api/v1/imodigi"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.config import settings
from app.core.exceptions import ImodigiError, NotFoundError
from app.models.listing_model import Listing
from app.repositories import imodigi_repository
from app.schemas.base_schema import ApiResponse, Meta
from app.schemas.imodigi_schema import (
    ImodigiCatalogValues,
    ImodigiExportRequest,
    ImodigiExportResponse,
    ImodigiExportRead,
    ImodigiLocationItem,
    ImodigiStoreRead,
)
from app.services import imodigi_service

router = APIRouter()


# ── Catalog / lookup endpoints ────────────────────────────────────────────

@router.get(
    "/stores",
    response_model=ApiResponse[list[ImodigiStoreRead]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_list_stores",
)
async def list_stores(request: Request):
    """Proxy GET /crm-stores.php — list active Imodigi stores."""
    stores = await imodigi_service.get_stores()
    return ok([ImodigiStoreRead(**s) for s in stores], "Stores retrieved", request)


@router.get(
    "/catalog",
    response_model=ApiResponse[ImodigiCatalogValues],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_catalog_values",
)
async def catalog_values(request: Request):
    """Proxy GET /crm-property-values.php — allowed values for property fields."""
    values = await imodigi_service.get_catalog_values()
    catalog = ImodigiCatalogValues(
        property_type=values.get("propertyType", []),
        business_type=values.get("businessType", []),
        state=values.get("state", []),
        availability=values.get("availability", []),
        energy_class=values.get("energyClass", []),
        country=values.get("country", []),
    )
    return ok(catalog, "Catalog values retrieved", request)


@router.get(
    "/locations",
    response_model=ApiResponse[list[ImodigiLocationItem]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_search_locations",
)
async def search_locations(
    request: Request,
    level: str = Query(..., description="country | region | district | county | parish"),
    country_id: int | None = Query(None),
    region_id: int | None = Query(None),
    district_id: int | None = Query(None),
    county_id: int | None = Query(None),
    q: str | None = Query(None, min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Proxy GET /crm-locations.php — search the Imodigi location hierarchy."""
    items = await imodigi_service.search_locations(
        level,
        country_id=country_id,
        region_id=region_id,
        district_id=district_id,
        county_id=county_id,
        q=q,
        limit=limit,
    )
    return ok([ImodigiLocationItem(**i) for i in items], "Locations retrieved", request)


# ── Export endpoints ──────────────────────────────────────────────────────

@router.post(
    "/export/{listing_id}",
    response_model=ApiResponse[ImodigiExportResponse],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_export_listing",
    status_code=200,
)
async def export_listing(
    listing_id: UUID,
    request: Request,
    payload: ImodigiExportRequest = ImodigiExportRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Export (create or update) a listing in the Imodigi CRM.

    Uses settings.imodigi_client_id by default; pass `client_id` in the body
    to override per-request.
    """
    client_id = payload.client_id or settings.imodigi_client_id
    if not client_id:
        raise ImodigiError("IMODIGI_CLIENT_ID is not configured. Provide it in the request body or set the environment variable.")

    # Load listing with media (needed for image URLs)
    listing = (
        await db.execute(
            select(Listing)
            .where(Listing.id == listing_id)
            .options(selectinload(Listing.media_assets))
        )
    ).scalar_one_or_none()
    if not listing:
        raise NotFoundError(f"Listing {listing_id} not found")

    # Check existing export record
    existing = await imodigi_repository.get_export_by_listing_id(db, listing_id)
    existing_imodigi_id = existing.imodigi_property_id if existing else None

    try:
        imodigi_id, imodigi_ref, action = await imodigi_service.export_listing(
            listing,
            client_id=client_id,
            existing_imodigi_id=existing_imodigi_id,
        )
        status = "published" if action == "created" else "updated"
        export_record = await imodigi_repository.upsert_export(
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
    except ImodigiError as exc:
        # Persist the failure so the caller can introspect it via GET /exports
        await imodigi_repository.upsert_export(
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

    return ok(
        ImodigiExportResponse(
            listing_id=listing_id,
            imodigi_property_id=export_record.imodigi_property_id,
            imodigi_reference=export_record.imodigi_reference,
            status=export_record.status,
            action=action,
        ),
        f"Listing {action} in Imodigi",
        request,
    )


@router.get(
    "/exports",
    response_model=ApiResponse[list[ImodigiExportRead]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_list_exports",
)
async def list_exports(
    request: Request,
    status: str | None = Query(None, description="Filter by status: pending | published | updated | failed"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all Imodigi export records with optional status filter."""
    rows, total = await imodigi_repository.list_exports(db, status=status, page=page, page_size=page_size)
    pages = (total + page_size - 1) // page_size if total else 0
    return ok(
        [ImodigiExportRead.model_validate(r) for r in rows],
        "Exports retrieved",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get(
    "/exports/{listing_id}",
    response_model=ApiResponse[ImodigiExportRead],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_get_export",
)
async def get_export(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the Imodigi export record for a specific listing."""
    record = await imodigi_repository.get_export_by_listing_id(db, listing_id)
    if not record:
        raise NotFoundError(f"No Imodigi export found for listing {listing_id}")
    return ok(ImodigiExportRead.model_validate(record), "Export retrieved", request)
