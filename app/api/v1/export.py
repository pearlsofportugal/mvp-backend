"""Export API router — download listings as CSV, JSON, or Excel. /api/v1/export

CORREÇÕES v2:
- `import io` duplicado removido
- `import csv` e `import json` movidos para o topo do ficheiro
- bare `except:` substituído por `except (TypeError, ValueError):`
- CSV e JSON usam streaming real com `yield_per()` em vez de carregar tudo em memória
- Excel mantém carregamento completo (openpyxl não suporta streaming nativo)
  mas adiciona aviso de limite de 5000 registos para evitar OOM
"""
import csv
import io
import json
from decimal import Decimal
from typing import AsyncIterator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.api.deps import get_db
from app.models.listing import Listing

router = APIRouter()

# Limite de segurança para exportação Excel (carregado inteiro em memória)
_EXCEL_MAX_ROWS = 5_000


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


# Nomes das colunas — definidos uma vez para garantir ordem consistente
_CSV_FIELDNAMES = list(_listing_to_dict.__annotations__.keys()) if hasattr(_listing_to_dict, '__annotations__') else None


async def _csv_row_generator(db: AsyncSession, query) -> AsyncIterator[str]:
    """Gerador assíncrono que produz linhas CSV em stream, linha a linha.

    Usa yield_per(200) para carregar 200 registos de cada vez em vez de todos
    de uma vez — evita OOM com volumes grandes.
    """
    output = io.StringIO()
    writer = None
    first = True

    async with db.stream(query) as result:
        async for partition in result.partitions(200):
            for row in partition:
                listing = row[0] if hasattr(row, '__iter__') and not isinstance(row, Listing) else row
                row_dict = _listing_to_dict(listing)

                if first:
                    writer = csv.DictWriter(output, fieldnames=list(row_dict.keys()))
                    writer.writeheader()
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)
                    first = False

                writer.writerow(row_dict)
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

    if first:
        # Sem resultados — emitir CSV vazio
        yield ""


async def _json_row_generator(db: AsyncSession, query) -> AsyncIterator[str]:
    """Gerador assíncrono que produz JSON em stream (array de objetos).

    Usa yield_per(200) — evita OOM com volumes grandes.
    """
    first = True
    yield "[\n"

    async with db.stream(query) as result:
        async for partition in result.partitions(200):
            for row in partition:
                listing = row[0] if hasattr(row, '__iter__') and not isinstance(row, Listing) else row
                row_dict = _listing_to_dict(listing)
                prefix = "" if first else ",\n"
                yield prefix + json.dumps(row_dict, ensure_ascii=False)
                first = False

    yield "\n]"


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
    """Export filtered listings as CSV (streaming — sem limite de memória)."""
    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    )
    return StreamingResponse(
        _csv_row_generator(db, query),
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
    """Export filtered listings as JSON (streaming — sem limite de memória)."""
    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    )
    return StreamingResponse(
        _json_row_generator(db, query),
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
    """Export filtered listings as Excel (.xlsx).

    NOTA: openpyxl não suporta streaming nativo — os dados são carregados em memória.
    Limitado a {_EXCEL_MAX_ROWS} registos para evitar OOM. Para exportações maiores,
    usa os endpoints /csv ou /json que suportam streaming real.
    """
    query = _build_export_query(
        district=district, county=county, property_type=property_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
    ).limit(_EXCEL_MAX_ROWS)

    result = await db.execute(query)
    listings = result.scalars().all()

    rows = [_listing_to_dict(listing) for listing in listings]

    wb = Workbook()
    ws = wb.active
    ws.title = "Listings"

    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)

        bold_font = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold_font

        for row_dict in rows:
            ws.append(list(row_dict.values()))

        for i, column_cells in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = get_column_letter(i)

            for cell in column_cells:
                try:
                    if cell.value:
                        val_len = len(str(cell.value))
                        if val_len > max_length:
                            max_length = val_len
                except (TypeError, ValueError):
                    # FIX: bare except substituído por exceções específicas
                    pass

            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width

        # Aviso no rodapé se o resultado foi truncado
        if len(listings) == _EXCEL_MAX_ROWS:
            ws.append([f"[Resultado truncado a {_EXCEL_MAX_ROWS} registos. Usa /csv ou /json para exportação completa.]"])
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