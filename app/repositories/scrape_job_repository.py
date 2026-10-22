"""Repository — data-access layer for ScrapeJob records."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_job_model import ScrapeJob


class ScrapeJobRepository:

    @staticmethod
    async def get_by_id(db: AsyncSession, job_id: UUID) -> ScrapeJob | None:
        return (
            await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_idempotency_key(db: AsyncSession, key: str) -> ScrapeJob | None:
        return (
            await db.execute(select(ScrapeJob).where(ScrapeJob.idempotency_key == key))
        ).scalar_one_or_none()

    @staticmethod
    async def fail_abandoned_jobs(
        db: AsyncSession, site_key: str, *, running_cutoff: datetime, pending_cutoff: datetime
    ) -> int:
        """Mark this site's dead jobs as failed so they stop blocking new ones.

        A ``running`` job whose heartbeat is older than *running_cutoff* (or that never had one) belongs to a
        worker that died; a ``pending`` job created before *pending_cutoff* was never picked up. Without this a
        crashed Cloud Run Job execution keeps the site "active" forever and every later trigger answers 409.
        """
        jobs = (
            await db.execute(
                select(ScrapeJob).where(
                    ScrapeJob.site_key == site_key,
                    or_(
                        and_(
                            ScrapeJob.status == "running",
                            or_(
                                ScrapeJob.last_heartbeat_at.is_(None),
                                ScrapeJob.last_heartbeat_at < running_cutoff,
                            ),
                        ),
                        and_(ScrapeJob.status == "pending", ScrapeJob.created_at < pending_cutoff),
                    ),
                )
            )
        ).scalars().all()
        for job in jobs:
            reason = (
                "Job marked failed: no heartbeat from its worker."
                if job.status == "running"
                else "Job marked failed: it was never started by a worker."
            )
            job.mark_failed(reason)
            job.add_log("error", reason)
        if jobs:
            await db.commit()
        return len(jobs)

    @staticmethod
    async def has_active_job(db: AsyncSession, site_key: str) -> bool:
        """Return True if there is a running or pending job for the given site_key."""
        result = await db.execute(
            select(func.count())
            .select_from(ScrapeJob)
            .where(
                ScrapeJob.site_key == site_key,
                ScrapeJob.status.in_(("running", "pending")),
            )
        )
        return result.scalar_one() > 0

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
