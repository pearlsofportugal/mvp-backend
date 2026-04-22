"""Pydantic schemas for generic background job responses (enrichment & Imodigi export)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BulkJobAccepted(BaseModel):
    """Returned immediately when a background bulk job is accepted (HTTP 202)."""

    job_id: UUID
    job_type: str
    status: str = "running"
    total: int = Field(..., ge=0, description="Number of items to process.")
    message: str


class BulkJobStatus(BaseModel):
    """Current state of a background bulk job — returned by polling / SSE."""

    job_id: UUID
    job_type: str
    status: str = Field(..., description="running | completed | failed")
    total: int
    done: int
    failed: int
    skipped: int
    progress_pct: float
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None
