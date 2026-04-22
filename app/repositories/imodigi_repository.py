"""Repository — data-access layer for ImodigiExport records."""
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.imodigi_export_model import ImodigiExport


class ImodigiRepository:
    @staticmethod
    async def get_export_by_listing_id(
        db: AsyncSession,
        listing_id: uuid.UUID,
    ) -> ImodigiExport | None:
        result = await db.execute(
            select(ImodigiExport).where(ImodigiExport.listing_id == listing_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
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

        count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = count_result.scalar_one()

        stmt = stmt.order_by(ImodigiExport.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = (await db.execute(stmt)).scalars().all()
        return rows, total

    @staticmethod
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
        now = datetime.now(timezone.utc)
        values: dict = {
            "listing_id": listing_id,
            "imodigi_client_id": imodigi_client_id,
            "status": status,
            "last_error": last_error,
            "updated_at": now,
        }
        if imodigi_property_id is not None:
            values["imodigi_property_id"] = imodigi_property_id
        if imodigi_reference is not None:
            values["imodigi_reference"] = imodigi_reference
        if status in ("published", "updated"):
            values["last_exported_at"] = now

        set_values = {k: v for k, v in values.items() if k != "listing_id"}

        stmt = (
            pg_insert(ImodigiExport)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["listing_id"],
                set_=set_values,
            )
        )
        await db.execute(stmt)
        await db.flush()
        return await db.scalar(select(ImodigiExport).where(ImodigiExport.listing_id == listing_id))

    @staticmethod
    async def delete_export_by_listing_id(
        db: AsyncSession,
        listing_id: uuid.UUID,
    ) -> bool:
        """Delete the export record for a listing. Returns True if a row was deleted."""
        result = await db.execute(
            delete(ImodigiExport).where(ImodigiExport.listing_id == listing_id)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_exports_by_listing_ids(
        db: AsyncSession,
        listing_ids: list[uuid.UUID],
    ) -> int:
        """Delete export records for a list of listings. Returns the number of deleted rows."""
        result = await db.execute(
            delete(ImodigiExport).where(ImodigiExport.listing_id.in_(listing_ids))
        )
        return result.rowcount

    @staticmethod
    async def delete_all_exports(db: AsyncSession) -> int:
        """Delete ALL export records. Returns the number of deleted rows."""
        result = await db.execute(delete(ImodigiExport))
        return result.rowcount
