"""Export API router — download listings as CSV, JSON, or Excel.
/api/v1/export
"""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, listing_filter_params
from app.api.responses import ERROR_RESPONSES
from app.config import settings  # noqa: F401 — re-exported so monkeypatch can reach settings.export_max_rows
from app.services.export_service import ExportService

router = APIRouter()


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
    filters: dict = Depends(listing_filter_params),
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
    filters: dict = Depends(listing_filter_params),
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
    filters: dict = Depends(listing_filter_params),
):
    """Export filtered listings as Excel (.xlsx)."""
    output = await ExportService.export_excel(db, filters)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=listings_export.xlsx"},
    )