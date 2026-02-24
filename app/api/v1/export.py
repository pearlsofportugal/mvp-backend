"""Export API router — download listings as CSV, JSON, or Excel. /api/v1/export"""
import io
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
import io
from app.api.deps import get_db
from app.models.listing import Listing

router = APIRouter()


def _build_export_query(**kwargs):
    """Build a filtered query for export."""
    query = select(Listing)
    filters = []

    if kwargs.get("district"):
        filters.append(Listing.district.ilike(f"%{kwargs['district']}%"))
    if kwargs.get("county"):
        filters.append(Listing.county.ilike(f"%{kwargs['county']}%"))
    if kwargs.get("property_type"):
        filters.append(Listing.property_type.ilike(f"%{kwargs['property_type']}%"))
    if kwargs.get("source_partner"):
        filters.append(Listing.source_partner == kwargs["source_partner"])
    if kwargs.get("scrape_job_id"):
        filters.append(Listing.scrape_job_id == kwargs["scrape_job_id"])
    if kwargs.get("price_min") is not None:
        filters.append(Listing.price_amount >= kwargs["price_min"])
    if kwargs.get("price_max") is not None:
        filters.append(Listing.price_amount <= kwargs["price_max"])

    if filters:
        query = query.where(and_(*filters))

    return query.order_by(Listing.created_at.desc())


def _listing_to_dict(listing: Listing) -> dict:
    """Convert a listing ORM object to a flat dict for export."""
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
        "price_amount": float(listing.price_amount) if listing.price_amount else None,
        "price_currency": listing.price_currency,
        "price_per_m2": float(listing.price_per_m2) if listing.price_per_m2 else None,
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


@router.get("/csv")
async def export_csv(
    db: AsyncSession = Depends(get_db),
    district: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    property_type: Optional[str] = Query(None),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
    price_min: Optional[Decimal] = Query(None),
    price_max: Optional[Decimal] = Query(None),
):
    """Export filtered listings as CSV."""
    import csv

    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    )
    result = await db.execute(query)
    listings = result.scalars().all()

    output = io.StringIO()
    if listings:
        rows = [_listing_to_dict(l) for l in listings]
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("")

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=listings_export.csv"},
    )


@router.get("/json")
async def export_json(
    db: AsyncSession = Depends(get_db),
    district: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    property_type: Optional[str] = Query(None),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
    price_min: Optional[Decimal] = Query(None),
    price_max: Optional[Decimal] = Query(None),
):
    """Export filtered listings as JSON."""
    import json

    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    )
    result = await db.execute(query)
    listings = result.scalars().all()

    rows = [_listing_to_dict(l) for l in listings]
    content = json.dumps(rows, ensure_ascii=False, indent=2)

    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=listings_export.json"},
    )


@router.get("/excel")
async def export_excel(
    db: AsyncSession = Depends(get_db),
    district: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    property_type: Optional[str] = Query(None),
    source_partner: Optional[str] = Query(None),
    scrape_job_id: Optional[UUID] = Query(None),
    price_min: Optional[Decimal] = Query(None),
    price_max: Optional[Decimal] = Query(None),
):
    """Export filtered listings as Excel (.xlsx) using openpyxl directly."""
    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    )
    result = await db.execute(query)
    listings = result.scalars().all()

    rows = [_listing_to_dict(l) for l in listings]

    # Criar o Workbook e a folha ativa
    wb = Workbook()
    ws = wb.active
    ws.title = "Listings"

    if rows:
        # 1. Escrever o Cabeçalho
        headers = list(rows[0].keys())
        ws.append(headers)

        # Estilo do cabeçalho: Negrito
        bold_font = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold_font

        # 2. Escrever os Dados
        for row_dict in rows:
            ws.append(list(row_dict.values()))

        # 3. Auto-width das colunas
        # Vamos iterar pelas colunas para calcular a largura
        for i, column_cells in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = get_column_letter(i)
            
            for cell in column_cells:
                try:
                    if cell.value:
                        val_len = len(str(cell.value))
                        if val_len > max_length:
                            max_length = val_len
                except:
                    pass
            
            # Ajustar largura (máximo de 50 para não ficar gigante)
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
    else:
        # Se não houver dados, apenas escrever uma mensagem ou cabeçalhos vazios
        ws.append(["No data found"])

    # Salvar para o buffer
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=listings_export.xlsx"},
    )