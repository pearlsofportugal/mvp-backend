"""Durable progress updates for non-scrape background work."""
from datetime import datetime, timezone
from typing import Any
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
    skipped: int | None = None, error: str | None = None, errors: list[str] | None = None,
    result: dict | None = None, status: str | None = None,
) -> BackgroundJob | None:
    """Update a durable job row. `error` appends a single new message;
    `errors` wholesale-replaces the list (used by periodic snapshot syncing,
    which already holds the authoritative in-memory list and just needs to
    mirror it — appending would double up entries already recorded).
    """
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
        elif errors is not None:
            job.errors = errors[-100:]
        if result is not None:
            job.result = result
        if status:
            job.status = status
            if status in {"completed", "failed"}:
                job.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)
        return job


def to_status_dict(job: BackgroundJob) -> dict[str, Any]:
    """Convert a durable BackgroundJob row into the same shape as
    bulk_job_store.BulkJobState.to_dict(), so API routes can serve a
    BulkJobStatus response from either source transparently — the in-memory
    store when this instance still holds the job, this DB row otherwise
    (process restart, or a different instance in a multi-instance deployment).
    """
    total = job.total or 0
    processed = job.done + job.failed + job.skipped
    progress_pct = 100.0 if total == 0 else round(processed / total * 100, 1)
    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "total": total,
        "done": job.done,
        "failed": job.failed,
        "skipped": job.skipped,
        "progress_pct": progress_pct,
        "errors": (job.errors or [])[-20:],
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
