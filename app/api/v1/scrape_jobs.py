"""Scrape Jobs API router — launch, monitor, and manage scraping jobs.
/api/v1/jobs
"""

import asyncio
import json
import time
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Security
from fastapi.security import APIKeyHeader

from app.api.deps import RequireApiKey, get_db, verify_api_key
from app.api.responses import ERROR_RESPONSES, ok
from app.config import settings
from app.core.exceptions import JobAlreadyRunningError, NotFoundError
from app.database import async_session_factory
from app.schemas.base_schema import ApiResponse
from app.schemas.scrape_job_schema import JobCreate, JobListRead, JobRead
from app.repositories.site_config_repository import SiteConfigRepository
from app.services.scrape_job_service import ScrapeJobService
from app.services.scraper_service import run_scrape_job

router = APIRouter()

_SSE_POLL_INTERVAL = 1.0    # seconds between DB reads while streaming
_SSE_HEARTBEAT_EVERY = 15   # emit heartbeat every N ticks
_SSE_MAX_DURATION = 3600    # max stream open duration in seconds (1 hour)
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

@router.post(
    "/trigger/{site_key}",
    response_model=ApiResponse[JobRead],
    status_code=201,
    responses={**ERROR_RESPONSES, 409: {"model": ApiResponse, "description": "A scrape job is already running for this site."}},
    operation_id="trigger_scheduled_job",
)
async def trigger_scheduled_job(
    site_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Security(verify_api_key),
):
    """Trigger a scheduled scrape job for a site.

    Called by Google Cloud Scheduler. This endpoint blocks until the scraping is
    fully completed so that Cloud Run allocates full CPU for the task.
    """
    site = await SiteConfigRepository.get_by_key(db, site_key)
    if not site or not site.is_active:
        raise NotFoundError(f"Site config '{site_key}' not found or inactive")

    start_url = site.schedule_start_url or site.base_url
    if not start_url:
        raise NotFoundError(f"Site '{site_key}' has no start URL configured")

    max_pages = site.schedule_max_pages or settings.default_max_pages

    payload = JobCreate(site_key=site_key, start_url=start_url, max_pages=max_pages)
    job = await ScrapeJobService.create_job(db, payload)
    
    await run_scrape_job(str(job.id))

    return ok(JobRead.model_validate(job), "Scheduled job executed and completed successfully", request)

# ---------------------------------------------------------------------------
# SSE — Server-Sent Events
# ---------------------------------------------------------------------------

async def _sse_job_stream(job_id: UUID, request: Request) -> AsyncIterator[str]:
    """
    Async generator that emits SSE events with live job progress.
    """
    tick = 0
    last_progress: dict | None = None
    last_status: str | None = None
    stream_started = time.monotonic()

    try:
        # 1. Validação inicial: Verifica se o job existe antes de iniciar o loop
        async with async_session_factory() as db:
            try:
                await ScrapeJobService.get_job(db, job_id)
            except Exception:
                yield _sse_event("error", {"message": f"Job {job_id} not found"})
                return

        # 2. Loop principal de monitorização
        while True:
            if await request.is_disconnected():
                break

            if time.monotonic() - stream_started > _SSE_MAX_DURATION:
                yield _sse_event("done", {"job_id": str(job_id), "message": "Stream max duration reached"})
                break

            # Abre uma sessão fresca Apenas para esta iteração (evita o Identity Map Cache)
            async with async_session_factory() as db:
                try:
                    job = await ScrapeJobService.get_job(db, job_id)
                except Exception:
                    yield _sse_event("error", {"message": "Job disappeared from database"})
                    break

                current_progress = job.progress or {}
                current_status = job.status

                # Deteta alterações de progresso ou estado
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

                # Se chegou a um estado final, envia o snapshot 'done' e quebra o loop
                if current_status in _SSE_TERMINAL_STATUSES:
                    yield _sse_event("done", {
                        "job_id": str(job_id),
                        "status": current_status,
                        "progress": current_progress,
                        "error_message": job.error_message,
                    })
                    break

            # A sessão fecha aqui ao sair do 'async with'. 
            # O link à BD fica livre enquanto a função aguarda no sleep.
            tick += 1
            if tick % _SSE_HEARTBEAT_EVERY == 0:
                yield _sse_event("heartbeat", {"tick": tick})

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    except asyncio.CancelledError:
        # Captura a desconexão nativa do cliente/browser (Client Disconnect)
        pass  
    except Exception as e:
        # Captura qualquer outro erro inesperado no stream
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