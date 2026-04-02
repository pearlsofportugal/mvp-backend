"""Export API router — download listings as CSV, JSON, or Excel.
/api/v1/export
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES
from app.services.export_service import ExportService

router = APIRouter()


def _export_filters(
    district: str | None = Query(None),
    county: str | None = Query(None),
    parish: str | None = Query(None),
    property_type: str | None = Query(None),
    typology: str | None = Query(None),
    listing_type: str | None = Query(None, pattern="^(sale|rent)$"),
    source_partner: str | None = Query(None),
    scrape_job_id: UUID | None = Query(None),
    price_min: Decimal | None = Query(None),
    price_max: Decimal | None = Query(None),
    area_min: float | None = Query(None),
    area_max: float | None = Query(None),
    bedrooms_min: int | None = Query(None),
    bedrooms_max: int | None = Query(None),
    has_garage: bool | None = Query(None),
    has_pool: bool | None = Query(None),
    has_elevator: bool | None = Query(None),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    search: str | None = Query(None),
) -> dict:
    return dict(
        district=district, county=county, parish=parish,
        property_type=property_type, typology=typology, listing_type=listing_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
        area_min=area_min, area_max=area_max,
        bedrooms_min=bedrooms_min, bedrooms_max=bedrooms_max,
        has_garage=has_garage, has_pool=has_pool, has_elevator=has_elevator,
        created_after=created_after, created_before=created_before,
        search=search,
    )


@router.get(
    "/csv",
    summary="Export CSV",
    operation_id="export_csv",
    responses={
        200: {"content": {"text/csv": {}}, "description": "CSV file download"},
        **ERROR_RESPONSES,
    },
)
async def export_csv(
    db: AsyncSession = Depends(get_db),
    filters: dict = Depends(_export_filters),
):
    """Export filtered listings as CSV."""
    output = await ExportService.export_csv(db, filters)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=listings_export.csv"},
    )


@router.get(
    "/json",
    summary="Export JSON",
    operation_id="export_json",
    responses={
        200: {"content": {"application/json": {}}, "description": "JSON file download"},
        **ERROR_RESPONSES,
    },
)
async def export_json(
    db: AsyncSession = Depends(get_db),
    filters: dict = Depends(_export_filters),
):
    """Export filtered listings as JSON."""
    content = await ExportService.export_json(db, filters)
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=listings_export.json"},
    )


@router.get(
    "/excel",
    summary="Export Excel",
    operation_id="export_excel",
    responses={
        200: {
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
            "description": "Excel (.xlsx) file download",
        },
        **ERROR_RESPONSES,
    },
)
async def export_excel(
    db: AsyncSession = Depends(get_db),
    filters: dict = Depends(_export_filters),
):
    """Export filtered listings as Excel (.xlsx)."""
    output = await ExportService.export_excel(db, filters)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=listings_export.xlsx"},
    )