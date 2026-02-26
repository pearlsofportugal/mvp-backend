"""Scrape Jobs API router — launch, monitor, and manage scraping jobs.
/api/v1/jobs

CORREÇÕES v2:
- SSE: reutiliza uma única sessão de DB por toda a duração do stream em vez de
  abrir/fechar uma sessão nova a cada tick (1 sessão/segundo por cliente conectado)
- `trace_id` calculado mas não usado em list_jobs removido (linha morta)
"""
import asyncio
import json
from typing import AsyncIterator, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import JobAlreadyRunningError, NotFoundError
from app.models.scrape_job import ScrapeJob
from app.models.site_config import SiteConfig
from app.schemas.scrape_job import JobCreate, JobListRead, JobRead
from app.schemas.base_schema import ApiResponse
from app.api.responses import ok
from app.services.scraper_service import run_scrape_job
from app.database import async_session_factory


router = APIRouter()


@router.post("", response_model=ApiResponse[JobRead], status_code=201)
async def create_job(
    request: Request,
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Launch a new scrape job. Runs in background (MVP: one job at a time per worker)."""
    running = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "running")
    )
    if running.scalar_one_or_none():
        raise JobAlreadyRunningError(
            "A scrape job is already running. Wait for it to finish or cancel it."
        )

    site = await db.execute(
        select(SiteConfig).where(SiteConfig.key == payload.site_key, SiteConfig.is_active.is_(True))
    )
    site_config = site.scalar_one_or_none()
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
    await db.commit()  # Commit BEFORE scheduling background task to avoid race condition
    await db.refresh(job)

    background_tasks.add_task(run_scrape_job, str(job.id))

    return ok(JobRead.model_validate(job), "Job created successfully", request)


@router.get("", response_model=ApiResponse[list[JobListRead]])
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

    result = await db.execute(query)
    total_result = await db.execute(count_query)
    jobs = result.scalars().all()
    total = total_result.scalar_one()

    # FIX: trace_id calculado mas nunca usado — removido (ok() lê request.state internamente)
    return ok(
        [JobListRead.model_validate(j) for j in jobs],
        "Jobs listed successfully",
        request,
        meta={"page": page, "page_size": page_size, "total": total},
    )


@router.get("/{job_id}", response_model=ApiResponse[JobRead])
async def get_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Get the status and progress of a scrape job."""
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")
    return ok(JobRead.model_validate(job), "Job retrieved successfully", request)


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobRead])
async def cancel_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Cancel a running or pending scrape job."""
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")
    if job.status not in ("pending", "running"):
        raise JobAlreadyRunningError(
            f"Job {job_id} cannot be cancelled (status: {job.status})"
        )

    job.mark_cancelled()
    # flush() é suficiente aqui — o commit é feito automaticamente pelo get_db dependency
    # no final do request (ver app/api/deps.py). O estado cancelled fica visível ao
    # scraper_service na próxima iteração via _is_cancelled().
    await db.flush()
    return ok(JobRead.model_validate(job), "Job cancelled successfully", request)


@router.delete("/{job_id}", response_model=ApiResponse[None], status_code=200)
async def delete_job(job_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a scrape job record."""
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Scrape job {job_id} not found")
    if job.status == "running":
        raise JobAlreadyRunningError("Cannot delete a running job. Cancel it first.")
    await db.delete(job)
    await db.flush()
    return ok(None, "Job deleted successfully", request)


# ---------------------------------------------------------------------------
# SSE — Server-Sent Events para progresso em tempo real
# ---------------------------------------------------------------------------

_SSE_POLL_INTERVAL = 1.0   # segundos entre leituras à DB durante o streaming
_SSE_HEARTBEAT_EVERY = 15  # enviar heartbeat a cada N ticks (evita timeout de proxies)
_SSE_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


async def _sse_job_stream(job_id: UUID, request: Request) -> AsyncIterator[str]:
    """
    Gerador assíncrono que emite eventos SSE com o progresso do job.

    Formato SSE (RFC 8895):
      event: <tipo>\\n
      data: <json>\\n
      \\n

    Tipos de eventos emitidos:
      - 'progress': atualização de contadores (pages_visited, listings_found, etc.)
      - 'status':   mudança de estado do job (running → completed, failed, cancelled)
      - 'heartbeat': keepalive — evita que proxies/load-balancers fechem a ligação idle
      - 'error':    job não encontrado ou erro interno no stream
      - 'done':     evento final antes de fechar o stream

    O stream fecha automaticamente quando:
      1. O job atinge um estado terminal (completed/failed/cancelled)
      2. O cliente fecha a ligação (request.is_disconnected())
      3. Ocorre um erro irrecuperável

    FIX: Usa uma única sessão de DB para todo o ciclo de vida do stream.
    A versão anterior abria e fechava uma sessão a cada tick (1s) — com 10 clientes
    SSE conectados isso gerava 10 sessões/segundo de pressão desnecessária no pool.
    A sessão é revalidada via db.expire_all() antes de cada query para garantir
    que os dados refletem o estado atual da DB (sem cache stale do identity map).
    """
    tick = 0
    last_progress: Optional[dict] = None
    last_status: Optional[str] = None

    try:
        # FIX: sessão única para todo o stream — aberta uma vez, reutilizada a cada tick
        async with async_session_factory() as db:
            # Verificar que o job existe antes de começar o stream
            result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                yield _sse_event("error", {"message": f"Job {job_id} not found"})
                return

            # Loop de streaming
            while True:
                # Verificar se o cliente desligou (evita streams órfãos no servidor)
                if await request.is_disconnected():
                    break

                # expire_all() invalida o identity map — força re-fetch da DB na próxima query.
                # Sem isto, o SQLAlchemy retornaria o objeto em cache sem ir à DB.
                db.expire_all()
                result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
                job = result.scalar_one_or_none()

                if not job:
                    yield _sse_event("error", {"message": "Job disappeared from database"})
                    break

                current_progress = job.progress or {}
                current_status = job.status

                # Emitir evento se algo mudou
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

                # Fechar stream se o job terminou
                if current_status in _SSE_TERMINAL_STATUSES:
                    yield _sse_event("done", {
                        "job_id": str(job_id),
                        "status": current_status,
                        "progress": current_progress,
                        "error_message": job.error_message,
                    })
                    break

                # Heartbeat periódico — mantém a ligação viva através de proxies
                tick += 1
                if tick % _SSE_HEARTBEAT_EVERY == 0:
                    yield _sse_event("heartbeat", {"tick": tick})

                await asyncio.sleep(_SSE_POLL_INTERVAL)

    except asyncio.CancelledError:
        # Cliente desligou — saída limpa sem log de erro
        pass
    except Exception as e:
        yield _sse_event("error", {"message": f"Stream error: {str(e)}"})


def _sse_event(event_type: str, data: dict) -> str:
    """Formata um evento SSE conforme RFC 8895."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.get(
    "/{job_id}/stream",
    summary="Stream job progress via Server-Sent Events",
    response_description="SSE stream com eventos de progresso do job",
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
    `@microsoft/fetch-event-source` no frontend (ver frontend/src/app/core/services/jobs.ts).
    """
    return StreamingResponse(
        _sse_job_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Desativa buffer do nginx — essencial para SSE
        },
    )