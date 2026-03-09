"""Scrape Jobs API router — launch, monitor, and manage scraping jobs.
/api/v1/jobs
"""

import asyncio
import json
import math
from typing import AsyncIterator, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.core.exceptions import AppException, JobAlreadyRunningError, NotFoundError
from app.database import async_session_factory
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig
from app.schemas.base_schema import ApiResponse, Meta
from app.schemas.scrape_job_schema import JobCreate, JobListRead, JobRead
from app.services.scraper_service import run_scrape_job

router = APIRouter()

_SSE_POLL_INTERVAL = 1.0    # seconds between DB reads while streaming
_SSE_HEARTBEAT_EVERY = 15   # emit heartbeat every N ticks
_SSE_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ApiResponse[JobRead], status_code=201, responses=ERROR_RESPONSES, operation_id="create_job")
async def create_job(
    request: Request,
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Launch a new scrape job. Runs in background (MVP: one job at a time per worker)."""
    running = (await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "running")
    )).scalar_one_or_none()
    if running:
        raise JobAlreadyRunningError(
            "A scrape job is already running. Wait for it to finish or cancel it."
        )

    site_config = (await db.execute(
        select(SiteConfig).where(SiteConfig.key == payload.site_key, SiteConfig.is_active.is_(True))
    )).scalar_one_or_none()
    if not site_config:
        raise NotFoundError(f"Site config '{payload.site_key}' not found or inactive")

    job = ScrapeJob(
        site_key=payload.site_key,
        base_url=site_config.base_url,
        start_url=payload.start_url,
        max_pages=payload.max_pages,
        status="pending",
        config=payload.config.model_dump() if payload.config else None,
        progress={"pages_visited": 0, "listings_found": 0, "listings_scraped": 0, "errors": 0},
    )
    db.add(job)
    await db.commit()   # commit BEFORE background task to avoid race condition
    await db.refresh(job)

    background_tasks.add_task(run_scrape_job, str(job.id))
    return ok(JobRead.model_validate(job), "Job created successfully", request)


@router.get("", response_model=ApiResponse[list[JobListRead]], responses=ERROR_RESPONSES, operation_id="list_jobs")
async def list_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None, pattern="^(pending|running|completed|failed|cancelled)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List scrape jobs with optional status filter."""
    query = select(ScrapeJob).order_by(desc(ScrapeJob.created_at))
    count_query = select(func.count()).select_from(ScrapeJob)

    if status:
        query = query.where(ScrapeJob.status == status)
        count_query = count_query.where(ScrapeJob.status == status)

    query = query.offset((page - 1) * page_size).limit(page_size)

    # FIX: both queries must be awaited before consuming results
    result = await db.execute(query)
    total_result = await db.execute(count_query)

    jobs = result.scalars().all()
    total = total_result.scalar_one()
    pages = math.ceil(total / page_size) if total > 0 else 0

    return ok(
        [JobListRead.model_validate(j) for j in jobs],
        "Jobs listed successfully",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get("/{job_id}", response_model=ApiResponse[JobRead], responses=ERROR_RESPONSES, operation_id="get_job")
async def get_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Get the status and progress of a scrape job."""
    job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")
    return ok(JobRead.model_validate(job), "Job retrieved successfully", request)


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobRead], responses=ERROR_RESPONSES, operation_id="cancel_job")
async def cancel_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Cancel a running or pending scrape job."""
    job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")

    # FIX: use AppException for invalid state — JobAlreadyRunningError is for "already running"
    if job.status not in ("pending", "running"):
        raise AppException(f"Job {job_id} cannot be cancelled (status: {job.status})")

    job.mark_cancelled()
    await db.commit()
    return ok(JobRead.model_validate(job), "Job cancelled successfully", request)


@router.delete("/{job_id}", response_model=ApiResponse[None], status_code=200, responses=ERROR_RESPONSES, operation_id="delete_job")
async def delete_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a scrape job record."""
    job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")
    if job.status == "running":
        raise AppException("Cannot delete a running job. Cancel it first.")

    await db.delete(job)
    await db.commit()   # FIX: flush → commit so deletion actually persists
    return ok(None, "Job deleted successfully", request)


# ---------------------------------------------------------------------------
# SSE — Server-Sent Events
# ---------------------------------------------------------------------------

async def _sse_job_stream(job_id: UUID, request: Request) -> AsyncIterator[str]:
    """
    Async generator that emits SSE events with live job progress.

    Event types:
      - 'progress'  — counter updates (pages_visited, listings_found, etc.)
      - 'status'    — job state change (pending → running → completed/failed/cancelled)
      - 'heartbeat' — keepalive every ~15 s to prevent proxy timeouts
      - 'done'      — final snapshot when the job reaches a terminal state
      - 'error'     — job not found or internal stream error

    The stream closes automatically when:
      1. The job reaches a terminal state (completed/failed/cancelled)
      2. The client disconnects (request.is_disconnected())
      3. An unrecoverable error occurs
    """
    tick = 0
    last_progress: Optional[dict] = None
    last_status: Optional[str] = None

    try:
        async with async_session_factory() as db:
            job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
            if not job:
                yield _sse_event("error", {"message": f"Job {job_id} not found"})
                return

        while True:
            if await request.is_disconnected():
                break

            async with async_session_factory() as db:
                job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()

            if not job:
                yield _sse_event("error", {"message": "Job disappeared from database"})
                break

            current_progress = job.progress or {}
            current_status = job.status

            if current_progress != last_progress or current_status != last_status:
                payload = {
                    "job_id": str(job_id),
                    "status": current_status,
                    "progress": current_progress,
                    "error_message": job.error_message,
                }
                event_type = "status" if current_status != last_status else "progress"
                yield _sse_event(event_type, payload)
                last_progress = current_progress
                last_status = current_status

            if current_status in _SSE_TERMINAL_STATUSES:
                yield _sse_event("done", {
                    "job_id": str(job_id),
                    "status": current_status,
                    "progress": current_progress,
                    "error_message": job.error_message,
                })
                break

            tick += 1
            if tick % _SSE_HEARTBEAT_EVERY == 0:
                yield _sse_event("heartbeat", {"tick": tick})

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    except asyncio.CancelledError:
        pass  # client disconnected — clean exit
    except Exception as e:
        yield _sse_event("error", {"message": f"Stream error: {str(e)}"})


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event per RFC 8895."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.get(
    "/{job_id}/stream",
    summary="Stream job progress via Server-Sent Events",
    response_description="SSE stream com eventos de progresso do job",
    operation_id="stream_job_progress",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream with live job progress events",
        },
        **ERROR_RESPONSES,
    },
)
async def stream_job_progress(job_id: UUID, request: Request):
    """
    Stream do progresso de um scraping job via Server-Sent Events (SSE).

    O cliente recebe eventos em tempo real sem necessidade de polling.
    A ligação fecha automaticamente quando o job termina.

    Eventos:
    - `progress` — contadores atualizados (pages_visited, listings_found, listings_scraped, errors)
    - `status`   — mudança de estado (pending → running → completed/failed/cancelled)
    - `heartbeat` — keepalive a cada ~15s
    - `done`     — snapshot final quando o job termina
    - `error`    — job não encontrado ou erro interno

    **Nota de autenticação:** inclui o header `X-API-Key` na ligação SSE.
    O EventSource nativo do browser não suporta headers — usa a biblioteca
    `@microsoft/fetch-event-source` no frontend.
    """
    return StreamingResponse(
        _sse_job_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering — required for SSE
        },
    )