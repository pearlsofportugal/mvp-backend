"""Persistence and querying for scrape-job operational events."""
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_job_event_model import ScrapeJobEvent


async def record_event(
    db: AsyncSession,
    job_id: UUID,
    event_type: str,
    message: str,
    *,
    level: str = "info",
    url: str | None = None,
    data: dict | None = None,
) -> ScrapeJobEvent:
    event = ScrapeJobEvent(
        scrape_job_id=job_id,
        event_type=event_type,
        level=level,
        message=message,
        url=url,
        data=data,
    )
    db.add(event)
    return event


async def list_events(
    db: AsyncSession, job_id: UUID, page: int = 1, page_size: int = 100
) -> tuple[list[ScrapeJobEvent], int]:
    stmt = select(ScrapeJobEvent).where(ScrapeJobEvent.scrape_job_id == job_id)
    total = await db.scalar(
        select(func.count()).select_from(ScrapeJobEvent).where(ScrapeJobEvent.scrape_job_id == job_id)
    ) or 0
    rows = (
        await db.execute(
            stmt.order_by(desc(ScrapeJobEvent.created_at)).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return list(rows), total
