"""AI enrichment API router — multi-locale SEO content generation.
/api/v1/enrichment/ai"""

import asyncio
import json
import time
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ok, ERROR_RESPONSES
from app.schemas.ai_enrichment_schema import (
    BulkEnrichmentRequest,
    EnrichmentStats,
    ListingTranslationRequest,
    ListingTranslationResponse,
)
from app.schemas.background_job_schema import BulkJobAccepted, BulkJobStatus
from app.schemas.base_schema import ApiResponse
from app.services.ai_enrichment_service import (
    enrich_translations_and_persist,
    get_enrichment_stats,
    get_listings_for_bulk_enrich,
    run_bulk_enrich_job,
    run_single_enrich_job,
)
from app.services.bulk_job_store import create_job, get_job

router = APIRouter()

_SSE_POLL_INTERVAL = 1.0
_SSE_HEARTBEAT_EVERY = 15
_SSE_MAX_DURATION = 3600
_SSE_TERMINAL_STATUSES = {"completed", "failed"}


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _sse_bulk_stream(job_id: UUID, request: Request) -> AsyncIterator[str]:
    """Async generator that streams BulkJobState progress as SSE events."""
    job = get_job(job_id)
    if job is None:
        yield _sse_event("error", {"message": f"Job {job_id} not found"})
        return

    tick = 0
    last_snapshot: dict | None = None
    stream_started = time.monotonic()

    try:
        while True:
            if await request.is_disconnected():
                break

            if time.monotonic() - stream_started > _SSE_MAX_DURATION:
                yield _sse_event("done", {"job_id": str(job_id), "message": "Stream max duration reached"})
                break

            job = get_job(job_id)
            if job is None:
                yield _sse_event("error", {"message": "Job not found"})
                break

            snapshot = job.to_dict()

            if snapshot != last_snapshot:
                event_type = "status" if last_snapshot is None or snapshot["status"] != last_snapshot.get("status") else "progress"
                yield _sse_event(event_type, snapshot)
                last_snapshot = snapshot

            if job.is_terminal:
                yield _sse_event("done", snapshot)
                break

            tick += 1
            if tick % _SSE_HEARTBEAT_EVERY == 0:
                yield _sse_event("heartbeat", {"tick": tick})

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        yield _sse_event("error", {"message": f"Stream error: {exc}"})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=ApiResponse[EnrichmentStats], responses=ERROR_RESPONSES, operation_id="enrichment_stats")
async def enrichment_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    source_partner: str | None = Query(None, description="Filter stats by source partner"),
):
    """Aggregated enrichment statistics across all listings."""
    stats = await get_enrichment_stats(db, source_partner)
    return ok(stats, "Enrichment stats retrieved successfully", request)


# ---------------------------------------------------------------------------
# Bulk enrichment — async (non-blocking)
# ---------------------------------------------------------------------------

@router.post(
    "/bulk",
    response_model=ApiResponse[BulkJobAccepted],
    responses=ERROR_RESPONSES,
    operation_id="bulk_enrich_listings",
    status_code=202,
)
async def bulk_enrich(
    payload: BulkEnrichmentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Start a background bulk enrichment job and return immediately (HTTP 202).

    Poll ``GET /bulk/jobs/{job_id}`` or stream ``GET /bulk/jobs/{job_id}/stream``
    to track progress.

    When ``listing_ids`` is provided, only those listings are processed.
    Otherwise, all unenriched listings (optionally filtered by ``source_partner``)
    are queued up to ``limit``.
    """
    listings = await get_listings_for_bulk_enrich(db, payload)
    listing_ids = [listing.id for listing in listings]
    job = create_job("enrichment", total=len(listing_ids))
    background_tasks.add_task(run_bulk_enrich_job, job.id, listing_ids, payload)
    return ok(
        BulkJobAccepted(
            job_id=job.id,
            job_type=job.job_type,
            total=len(listing_ids),
            message=f"Bulk enrichment started for {len(listing_ids)} listing(s). Track progress via /bulk/jobs/{job.id}/stream",
        ),
        "Bulk enrichment job accepted",
        request,
    )


@router.get(
    "/bulk/jobs/{job_id}",
    response_model=ApiResponse[BulkJobStatus],
    responses=ERROR_RESPONSES,
    operation_id="get_bulk_enrich_job",
)
async def get_bulk_job(job_id: UUID, request: Request):
    """Poll the current status of a background bulk enrichment job."""
    from app.core.exceptions import NotFoundError

    job = get_job(job_id)
    if job is None:
        raise NotFoundError(f"Bulk job {job_id} not found")
    return ok(BulkJobStatus(**job.to_dict()), "Job status retrieved", request)


@router.get(
    "/bulk/jobs/{job_id}/stream",
    summary="Stream bulk enrichment progress via SSE",
    operation_id="stream_bulk_enrich_job",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream with live bulk enrichment progress",
        },
        **ERROR_RESPONSES,
    },
)
async def stream_bulk_enrich_job(job_id: UUID, request: Request):
    """Stream the progress of a bulk enrichment job via Server-Sent Events.

    Events: ``progress``, ``status``, ``heartbeat``, ``done``, ``error``.

    The stream closes automatically when the job reaches a terminal state
    (``completed`` or ``failed``).
    """
    return StreamingResponse(
        _sse_bulk_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Single-listing translation — sync + async variants
# ---------------------------------------------------------------------------

@router.post(
    "/translations",
    response_model=ApiResponse[ListingTranslationResponse],
    responses=ERROR_RESPONSES,
    operation_id="translate_listing",
)
async def translate_listing(
    payload: ListingTranslationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate multi-locale SEO content (EN, PT, ES, FR, DE) from original scraped data.

    All locales are generated independently in a single AI call — no chaining between languages.

    - **apply=False** (default): AI is called for locales that do not already have stored content.
      Returns a preview without writing to the database.
    - **apply=True**: Persists the ``translation_values`` provided by the caller.
      AI is NOT called in this path — supply the output from a prior apply=False call.
    - **force=True**: Regenerates locales even if they already have stored translations.

    For a non-blocking variant (returns immediately), use ``POST /translations/async``.
    """
    response = await enrich_translations_and_persist(db, payload.listing_id, payload)
    message = (
        f"Translations applied for locales: {', '.join(response.locales_generated)}"
        if response.applied
        else f"Translations generated for: {', '.join(response.locales_generated) or 'none (all cached)'}"
    )
    return ok(response, message, request)


@router.post(
    "/translations/async",
    response_model=ApiResponse[BulkJobAccepted],
    responses=ERROR_RESPONSES,
    operation_id="translate_listing_async",
    status_code=202,
)
async def translate_listing_async(
    payload: ListingTranslationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Start a non-blocking single-listing enrichment job and return immediately (HTTP 202).

    Only valid for ``apply=False`` (AI generation path).  To persist caller-supplied
    translations, use the synchronous ``POST /translations`` with ``apply=True``.

    Poll ``GET /bulk/jobs/{job_id}`` or stream ``GET /bulk/jobs/{job_id}/stream``
    to track progress and retrieve the generated translations from ``job.result``.
    """
    if payload.apply:
        from app.core.exceptions import AppException
        raise AppException("Use POST /translations with apply=True to persist translations synchronously.")

    job = create_job("enrichment", total=1)
    background_tasks.add_task(run_single_enrich_job, job.id, payload.listing_id, payload)
    return ok(
        BulkJobAccepted(
            job_id=job.id,
            job_type=job.job_type,
            total=1,
            message=f"Translation job started. Track progress via /bulk/jobs/{job.id}/stream",
        ),
        "Translation job accepted",
        request,
    )
