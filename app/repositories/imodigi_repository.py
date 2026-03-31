"""Repository — data-access layer for ImodigiExport records."""
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.imodigi_export_model import ImodigiExport


async def get_export_by_listing_id(
    db: AsyncSession,
    listing_id: uuid.UUID,
) -> ImodigiExport | None:
    result = await db.execute(
        select(ImodigiExport).where(ImodigiExport.listing_id == listing_id)
    )
    return result.scalar_one_or_none()


async def list_exports(
    db: AsyncSession,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[Sequence[ImodigiExport], int]:
    stmt = select(ImodigiExport)
    if status:
        stmt = stmt.where(ImodigiExport.status == status)

    from sqlalchemy import func
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar_one()

    stmt = stmt.order_by(ImodigiExport.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return rows, total


async def upsert_export(
    db: AsyncSession,
    *,
    listing_id: uuid.UUID,
    imodigi_property_id: int | None,
    imodigi_reference: str | None,
    imodigi_client_id: int,
    status: str,
    last_error: str | None = None,
) -> ImodigiExport:
    record = await get_export_by_listing_id(db, listing_id)

    now = datetime.now(timezone.utc)
    if record is None:
        record = ImodigiExport(
            listing_id=listing_id,
            imodigi_property_id=imodigi_property_id,
            imodigi_reference=imodigi_reference,
            imodigi_client_id=imodigi_client_id,
            status=status,
            last_error=last_error,
            last_exported_at=now if status in ("published", "updated") else None,
        )
        db.add(record)
    else:
        if imodigi_property_id is not None:
            record.imodigi_property_id = imodigi_property_id
        if imodigi_reference is not None:
            record.imodigi_reference = imodigi_reference
        record.imodigi_client_id = imodigi_client_id
        record.status = status
        record.last_error = last_error
        if status in ("published", "updated"):
            record.last_exported_at = now
        record.updated_at = now

    await db.flush()
    return record
