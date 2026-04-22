"""In-memory store for short-lived background jobs (enrichment & Imodigi bulk export).

Jobs are kept only for the lifetime of the process. State is stored in a module-level
dict keyed by job UUID — no DB persistence required for these ephemeral operations.

Concurrency note: all mutations go through plain dict accesses which are GIL-protected
in CPython. This is safe for asyncio code running in a single event-loop thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

# Supported job types
JOB_TYPE_ENRICHMENT = "enrichment"
JOB_TYPE_IMODIGI_EXPORT = "imodigi_export"

# Supported statuses
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED}

_JOBS: dict[UUID, BulkJobState] = {}


@dataclass
class BulkJobState:
    id: UUID
    job_type: str
    status: str
    total: int
    done: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    result: Any = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def progress_pct(self) -> float:
        if self.total == 0:
            return 100.0
        return round((self.done + self.failed + self.skipped) / self.total * 100, 1)

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "job_type": self.job_type,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "progress_pct": self.progress_pct(),
            "errors": self.errors[-20:],  # cap at 20 latest errors
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def create_job(job_type: str, total: int) -> BulkJobState:
    """Register a new job in the store and return its initial state."""
    job = BulkJobState(id=uuid4(), job_type=job_type, status=STATUS_RUNNING, total=total)
    _JOBS[job.id] = job
    return job


def get_job(job_id: UUID) -> BulkJobState | None:
    """Return the job state or None if the job is unknown / has been evicted."""
    return _JOBS.get(job_id)


def evict_completed_jobs(max_age_seconds: int = 3600) -> int:
    """Remove terminal jobs older than *max_age_seconds*. Returns eviction count."""
    now = datetime.now(timezone.utc)
    to_remove = [
        jid
        for jid, job in _JOBS.items()
        if job.is_terminal
        and job.finished_at is not None
        and (now - job.finished_at).total_seconds() > max_age_seconds
    ]
    for jid in to_remove:
        del _JOBS[jid]
    return len(to_remove)
