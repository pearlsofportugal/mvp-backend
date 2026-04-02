"""Repository — data-access layer for ScrapeJob records."""
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_job_model import ScrapeJob


class ScrapeJobRepository:

    @staticmethod
    async def get_by_id(db: AsyncSession, job_id: UUID) -> ScrapeJob | None:
        return (
            await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
        ).scalar_one_or_none()

    @staticmethod
    async def get_running(db: AsyncSession) -> ScrapeJob | None:
        return (
            await db.execute(
                select(ScrapeJob)
                .where(ScrapeJob.status == "running")
                .order_by(desc(ScrapeJob.started_at), desc(ScrapeJob.created_at))
                .limit(1)
            )
        ).scalars().first()

    @staticmethod
    async def get_all(
        db: AsyncSession,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ScrapeJob], int]:
        query = select(ScrapeJob).order_by(desc(ScrapeJob.created_at))
        count_query = select(func.count()).select_from(ScrapeJob)
        if status:
            query = query.where(ScrapeJob.status == status)
            count_query = count_query.where(ScrapeJob.status == status)
        query = query.offset((page - 1) * page_size).limit(page_size)
        jobs = (await db.execute(query)).scalars().all()
        total = (await db.execute(count_query)).scalar_one()
        return jobs, total

    @staticmethod
    async def create(db: AsyncSession, job: ScrapeJob) -> ScrapeJob:
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

    @staticmethod
    async def save(db: AsyncSession, job: ScrapeJob) -> ScrapeJob:
        await db.commit()
        await db.refresh(job)
        return job

    @staticmethod
    async def delete(db: AsyncSession, job: ScrapeJob) -> None:
        await db.delete(job)
        await db.commit()
