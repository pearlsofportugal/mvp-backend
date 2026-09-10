from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sold_listing_model import SoldListingUrl


class SoldListingRepository:

    @staticmethod
    async def is_recently_confirmed_sold(db: AsyncSession, source_url: str, ttl_days: int) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        result = await db.execute(
            select(SoldListingUrl.id).where(
                SoldListingUrl.source_url == source_url,
                SoldListingUrl.last_confirmed_at >= cutoff,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def mark_sold(db: AsyncSession, source_partner: str, source_url: str) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(SoldListingUrl)
            .values(source_partner=source_partner, source_url=source_url, last_confirmed_at=now)
            .on_conflict_do_update(
                index_elements=[SoldListingUrl.source_url],
                set_={"last_confirmed_at": now},
            )
        )
        await db.execute(stmt)

    @staticmethod
    async def unmark_sold(db: AsyncSession, source_url: str) -> None:
        await db.execute(delete(SoldListingUrl).where(SoldListingUrl.source_url == source_url))
