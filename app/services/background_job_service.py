"""Durable progress updates for non-scrape background work."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job_model import BackgroundJob


async def create_job(db: AsyncSession, job_type: str, total: int, payload: dict | None = None) -> BackgroundJob:
    job = BackgroundJob(job_type=job_type, total=total, payload=payload, status="running", errors=[])
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job(db: AsyncSession, job_id: UUID) -> BackgroundJob | None:
    return await db.get(BackgroundJob, job_id)


async def update_job(
    job_id: UUID, *, done: int | None = None, failed: int | None = None,
    skipped: int | None = None, error: str | None = None, result: dict | None = None,
    status: str | None = None,
) -> BackgroundJob | None:
    from app.database import async_session_factory
    async with async_session_factory() as db:
        job = await db.get(BackgroundJob, job_id)
        if job is None:
            return None
        if done is not None:
            job.done = done
        if failed is not None:
            job.failed = failed
        if skipped is not None:
            job.skipped = skipped
        if error:
            job.errors = [*(job.errors or [])[-99:], error]
        if result is not None:
            job.result = result
        if status:
            job.status = status
            if status in {"completed", "failed"}:
                job.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)
        return job
