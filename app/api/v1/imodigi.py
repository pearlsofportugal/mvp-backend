"""Imodigi CRM API router — publish listings to the Imodigi platform.
/api/v1/imodigi"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.config import settings
from app.core.exceptions import ImodigiError
from app.schemas.base_schema import ApiResponse, Meta
from app.schemas.imodigi_schema import (
    ImodigiCatalogValues,
    ImodigiExportRequest,
    ImodigiExportResponse,
    ImodigiExportRead,
    ImodigiLocationItem,
    ImodigiStoreRead,
)
from app.services.imodigi_service import (
    export_listing_to_crm,
    get_catalog_values,
    get_export_record,
    get_stores,
    list_export_records,
    search_locations,
)

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
    stores = await get_stores()
    return ok([ImodigiStoreRead(**s) for s in stores], "Stores retrieved", request)


@router.get(
    "/catalog",
    response_model=ApiResponse[ImodigiCatalogValues],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_catalog_values",
)
async def catalog_values(request: Request):
    """Proxy GET /crm-property-values.php — allowed values for property fields."""
    values = await get_catalog_values()
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
async def search_imodigi_locations(
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
    items = await search_locations(
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
    "/publish/{listing_id}",
    response_model=ApiResponse[ImodigiExportResponse],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_publish_listing",
    status_code=200,
)
async def publish_listing(
    listing_id: UUID,
    request: Request,
    payload: ImodigiExportRequest = ImodigiExportRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Publish (create or update) a listing in the Imodigi CRM.

    Uses settings.imodigi_client_id by default; pass `client_id` in the body
    to override per-request.
    """
    client_id = payload.client_id or settings.imodigi_client_id
    if not client_id:
        raise ImodigiError(
            "IMODIGI_CLIENT_ID is not configured. Provide it in the request body or set the environment variable."
        )
    export_record, action = await export_listing_to_crm(db, listing_id, client_id)
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
    "/publications",
    response_model=ApiResponse[list[ImodigiExportRead]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_list_publications",
)
async def list_publications(
    request: Request,
    status: str | None = Query(None, description="Filter by status: pending | published | updated | failed"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all Imodigi publication records with optional status filter."""
    rows, total = await list_export_records(db, status=status, page=page, page_size=page_size)
    pages = (total + page_size - 1) // page_size if total else 0
    return ok(
        [ImodigiExportRead.model_validate(r) for r in rows],
        "Exports retrieved",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get(
    "/publications/{listing_id}",
    response_model=ApiResponse[ImodigiExportRead],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_get_publication",
)
async def get_publication(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the Imodigi publication record for a specific listing."""
    record = await get_export_record(db, listing_id)
    return ok(ImodigiExportRead.model_validate(record), "Export retrieved", request)
