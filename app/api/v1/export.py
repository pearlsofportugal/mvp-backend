"""Export API router — download listings as CSV, JSON, or Excel.
/api/v1/export
"""

import csv
import io
import json
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES
from app.config import settings
from app.core.exceptions import ExportError
from app.models.listing_model import Listing

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_export_query(
    district: Optional[str] = None,
    county: Optional[str] = None,
    property_type: Optional[str] = None,
    source_partner: Optional[str] = None,
    scrape_job_id: Optional[UUID] = None,
    price_min: Optional[Decimal] = None,
    price_max: Optional[Decimal] = None,
):
    """Build a filtered, ordered query for listing export."""
    filters = []
    if district:
        filters.append(Listing.district.ilike(f"%{district}%"))
    if county:
        filters.append(Listing.county.ilike(f"%{county}%"))
    if property_type:
        filters.append(Listing.property_type.ilike(f"%{property_type}%"))
    if source_partner:
        filters.append(Listing.source_partner == source_partner)
    if scrape_job_id:
        filters.append(Listing.scrape_job_id == scrape_job_id)
    if price_min is not None:
        filters.append(Listing.price_amount >= price_min)
    if price_max is not None:
        filters.append(Listing.price_amount <= price_max)

    query = select(Listing)
    if filters:
        query = query.where(and_(*filters))
    return query.order_by(Listing.created_at.desc())


def _listing_to_dict(listing: Listing) -> dict:
    """Convert a listing ORM object to a flat dict suitable for export."""
    return {
        "id": str(listing.id),
        "partner_id": listing.partner_id,
        "source_partner": listing.source_partner,
        "source_url": listing.source_url,
        "title": listing.title,
        "listing_type": listing.listing_type,
        "property_type": listing.property_type,
        "typology": listing.typology,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "floor": listing.floor,
        "price_amount": float(listing.price_amount) if listing.price_amount is not None else None,
        "price_currency": listing.price_currency,
        "price_per_m2": float(listing.price_per_m2) if listing.price_per_m2 is not None else None,
        "area_useful_m2": listing.area_useful_m2,
        "area_gross_m2": listing.area_gross_m2,
        "area_land_m2": listing.area_land_m2,
        "district": listing.district,
        "county": listing.county,
        "parish": listing.parish,
        "full_address": listing.full_address,
        "latitude": listing.latitude,
        "longitude": listing.longitude,
        "has_garage": listing.has_garage,
        "has_elevator": listing.has_elevator,
        "has_balcony": listing.has_balcony,
        "has_air_conditioning": listing.has_air_conditioning,
        "has_pool": listing.has_pool,
        "energy_certificate": listing.energy_certificate,
        "construction_year": listing.construction_year,
        "advertiser": listing.advertiser,
        "contacts": listing.contacts,
        "description": listing.description,
        "enriched_description": listing.enriched_description,
        "description_quality_score": listing.description_quality_score,
        "meta_description": listing.meta_description,
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "updated_at": listing.updated_at.isoformat() if listing.updated_at else None,
    }


async def _load_export_rows(db: AsyncSession, filters: dict) -> list[Listing]:
    """Load export rows with a hard cap to protect API memory usage."""
    query = _build_export_query(**filters).limit(settings.export_max_rows + 1)
    listings = (await db.execute(query)).scalars().all()
    if len(listings) > settings.export_max_rows:
        raise ExportError(
            f"Export exceeds maximum row limit of {settings.export_max_rows}. Refine filters and try again."
        )
    return listings


# ---------------------------------------------------------------------------
# Shared query params (DRY — avoids repeating 7 Query() declarations)
# ---------------------------------------------------------------------------

def _export_filters(
    district: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    property_type: Optional[str] = Query(None),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
    price_min: Optional[Decimal] = Query(None),
    price_max: Optional[Decimal] = Query(None),
):
    return dict(
        district=district,
        county=county,
        property_type=property_type,
        source_partner=source_partner,
        scrape_job_id=scrape_job_id,
        price_min=price_min,
        price_max=price_max,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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
    listings = await _load_export_rows(db, filters)
    output = io.StringIO()

    if listings:
        rows = [_listing_to_dict(l) for l in listings]
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    output.seek(0)
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
    listings = await _load_export_rows(db, filters)
    rows = [_listing_to_dict(l) for l in listings]
    content = json.dumps(rows, ensure_ascii=False, indent=2)

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
    listings = await _load_export_rows(db, filters)
    rows = [_listing_to_dict(l) for l in listings]

    wb = Workbook()
    ws = wb.active
    ws.title = "Listings"

    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)

        bold = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold

        for row_dict in rows:
            ws.append(list(row_dict.values()))

        # Auto-width columns (capped at 50)
        for i, col_cells in enumerate(ws.columns, 1):
            max_len = max(
                (len(str(cell.value)) for cell in col_cells if cell.value is not None),
                default=0,
            )
            ws.column_dimensions[get_column_letter(i)].width = min(max_len + 2, 50)
    else:
        ws.append(["No data found"])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=listings_export.xlsx"},
    )