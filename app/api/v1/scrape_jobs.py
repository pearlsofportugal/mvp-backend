"""Scrape Jobs API router — launch, monitor, and manage scraping jobs.
/api/v1/jobs
"""

import asyncio
import json
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.database import async_session_factory
from app.models.scrape_job_model import ScrapeJob
from app.schemas.base_schema import ApiResponse
from app.schemas.scrape_job_schema import JobCreate, JobListRead, JobRead
from app.services.scrape_job_service import ScrapeJobService
from app.services.scraper_service import recover_stale_jobs, run_scrape_job

router = APIRouter()

_SSE_POLL_INTERVAL = 1.0    # seconds between DB reads while streaming
_SSE_HEARTBEAT_EVERY = 15   # emit heartbeat every N ticks
_SSE_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ApiResponse[JobRead], status_code=201, responses={**ERROR_RESPONSES, 409: {"model": ApiResponse, "description": "A scrape job is already running for this site."}}, operation_id="create_job")
async def create_job(
    request: Request,
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Launch a new scrape job. Runs in background (MVP: one job at a time per worker)."""
    await recover_stale_jobs(db)
    job = await ScrapeJobService.create_job(db, payload)
    background_tasks.add_task(run_scrape_job, str(job.id))
    return ok(JobRead.model_validate(job), "Job created successfully", request)


@router.get("", response_model=ApiResponse[list[JobListRead]], responses=ERROR_RESPONSES, operation_id="list_jobs")
async def list_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, pattern="^(pending|running|completed|failed|cancelled)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List scrape jobs with optional status filter."""
    jobs, meta = await ScrapeJobService.list_jobs(db, status=status, page=page, page_size=page_size)
    return ok([JobListRead.model_validate(j) for j in jobs], "Jobs listed successfully", request, meta=meta)


@router.get("/{job_id}", response_model=ApiResponse[JobRead], responses=ERROR_RESPONSES, operation_id="get_job")
async def get_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Get the status and progress of a scrape job."""
    job = await ScrapeJobService.get_job(db, job_id)
    return ok(JobRead.model_validate(job), "Job retrieved successfully", request)


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobRead], responses=ERROR_RESPONSES, operation_id="cancel_job")
async def cancel_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Cancel a running or pending scrape job."""
    job, message = await ScrapeJobService.cancel_job(db, job_id)
    return ok(JobRead.model_validate(job), message, request)


@router.delete("/{job_id}", response_model=ApiResponse[None], status_code=200, responses=ERROR_RESPONSES, operation_id="delete_job")
async def delete_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a scrape job record."""
    await ScrapeJobService.delete_job(db, job_id)
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
    last_progress: dict | None = None
    last_status: str | None = None

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