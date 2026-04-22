"""Imodigi CRM API router — publish listings to the Imodigi platform.
/api/v1/imodigi"""
import asyncio
import json
import time
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.config import settings
from app.core.exceptions import ImodigiError
from app.schemas.background_job_schema import BulkJobAccepted, BulkJobStatus
from app.schemas.base_schema import ApiResponse, Meta
from app.schemas.imodigi_schema import (
    ImodigiBulkExportRequest,
    ImodigiCatalogValues,
    ImodigiExportRequest,
    ImodigiExportResponse,
    ImodigiExportRead,
    ImodigiLocationItem,
    ImodigiResetRequest,
    ImodigiStoreRead,
)
from app.services.bulk_job_store import create_job, get_job
from app.services.imodigi_service import (
    export_listing_to_crm,
    get_catalog_values,
    get_export_record,
    get_listing_ids_for_bulk_imodigi,
    get_stores,
    list_export_records,
    reset_export_record,
    reset_export_records,
    run_bulk_imodigi_job,
    search_locations,
)

router = APIRouter()

_SSE_POLL_INTERVAL = 1.0
_SSE_HEARTBEAT_EVERY = 15
_SSE_MAX_DURATION = 3600
_SSE_TERMINAL_STATUSES = {"completed", "failed"}


# ── Catalog / lookup endpoints ────────────────────────────────────────────

@router.get(
    "/stores",
    response_model=ApiResponse[list[ImodigiStoreRead]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_list_stores",
)
async def list_stores(request: Request):
    """Proxy GET /crm-stores.php — list active Imodigi stores."""
    stores = await get_stores()
    return ok([ImodigiStoreRead(**s) for s in stores], "Stores retrieved", request)


@router.get(
    "/catalog",
    response_model=ApiResponse[ImodigiCatalogValues],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_catalog_values",
)
async def catalog_values(request: Request):
    """Proxy GET /crm-property-values.php — allowed values for property fields."""
    values = await get_catalog_values()
    catalog = ImodigiCatalogValues(
        property_type=values.get("propertyType", []),
        business_type=values.get("businessType", []),
        state=values.get("state", []),
        availability=values.get("availability", []),
        energy_class=values.get("energyClass", []),
        country=values.get("country", []),
    )
    return ok(catalog, "Catalog values retrieved", request)


@router.get(
    "/locations",
    response_model=ApiResponse[list[ImodigiLocationItem]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_search_locations",
)
async def search_imodigi_locations(
    request: Request,
    level: str = Query(..., description="country | region | district | county | parish"),
    country_id: int | None = Query(None),
    region_id: int | None = Query(None),
    district_id: int | None = Query(None),
    county_id: int | None = Query(None),
    q: str | None = Query(None, min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Proxy GET /crm-locations.php — search the Imodigi location hierarchy."""
    items = await search_locations(
        level,
        country_id=country_id,
        region_id=region_id,
        district_id=district_id,
        county_id=county_id,
        q=q,
        limit=limit,
    )
    return ok([ImodigiLocationItem(**i) for i in items], "Locations retrieved", request)


# ── Bulk export endpoints ─────────────────────────────────────────────────

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _sse_imodigi_stream(job_id: UUID, request: Request) -> AsyncIterator[str]:
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


@router.post(
    "/publish/bulk",
    response_model=ApiResponse[BulkJobAccepted],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_bulk_publish",
    status_code=202,
)
async def bulk_publish(
    payload: ImodigiBulkExportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Start a background bulk export job to Imodigi and return immediately (HTTP 202).

    When ``listing_ids`` is provided, only those listings are exported.
    Otherwise, all listings not yet published (or previous failures) are queued up
    to ``limit``.

    Poll ``GET /publish/jobs/{job_id}`` or stream ``GET /publish/jobs/{job_id}/stream``
    to track progress.
    """
    client_id = payload.client_id or settings.imodigi_client_id
    if not client_id:
        raise ImodigiError(
            "IMODIGI_CLIENT_ID is not configured. Provide it in the request body or set the environment variable."
        )
    listing_ids = await get_listing_ids_for_bulk_imodigi(db, payload.listing_ids, payload.limit)
    job = create_job("imodigi_export", total=len(listing_ids))
    background_tasks.add_task(run_bulk_imodigi_job, job.id, listing_ids, client_id)
    return ok(
        BulkJobAccepted(
            job_id=job.id,
            job_type=job.job_type,
            total=len(listing_ids),
            message=f"Bulk Imodigi export started for {len(listing_ids)} listing(s). Track via /publish/jobs/{job.id}/stream",
        ),
        "Bulk Imodigi export job accepted",
        request,
    )


@router.get(
    "/publish/jobs/{job_id}",
    response_model=ApiResponse[BulkJobStatus],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_get_bulk_job",
)
async def get_bulk_publish_job(job_id: UUID, request: Request):
    """Poll the current status of a background bulk Imodigi export job."""
    from app.core.exceptions import NotFoundError

    job = get_job(job_id)
    if job is None:
        raise NotFoundError(f"Bulk job {job_id} not found")
    return ok(BulkJobStatus(**job.to_dict()), "Job status retrieved", request)


@router.get(
    "/publish/jobs/{job_id}/stream",
    summary="Stream bulk Imodigi export progress via SSE",
    operation_id="imodigi_stream_bulk_job",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream with live bulk Imodigi export progress",
        },
        **ERROR_RESPONSES,
    },
)
async def stream_bulk_publish_job(job_id: UUID, request: Request):
    """Stream the progress of a bulk Imodigi export job via Server-Sent Events.

    Events: ``progress``, ``status``, ``heartbeat``, ``done``, ``error``.

    The stream closes automatically when the job reaches a terminal state.
    """
    return StreamingResponse(
        _sse_imodigi_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Single-listing export endpoints ──────────────────────────────────────

@router.post(
    "/publish/{listing_id}",
    response_model=ApiResponse[ImodigiExportResponse],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_publish_listing",
    status_code=200,
)
async def publish_listing(
    listing_id: UUID,
    request: Request,
    payload: ImodigiExportRequest = ImodigiExportRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Publish (create or update) a listing in the Imodigi CRM.

    Uses settings.imodigi_client_id by default; pass `client_id` in the body
    to override per-request.
    """
    client_id = payload.client_id or settings.imodigi_client_id
    if not client_id:
        raise ImodigiError(
            "IMODIGI_CLIENT_ID is not configured. Provide it in the request body or set the environment variable."
        )
    export_record, action = await export_listing_to_crm(db, listing_id, client_id)
    return ok(
        ImodigiExportResponse(
            listing_id=listing_id,
            imodigi_property_id=export_record.imodigi_property_id,
            imodigi_reference=export_record.imodigi_reference,
            status=export_record.status,
            action=action,
        ),
        f"Listing {action} in Imodigi",
        request,
    )


@router.get(
    "/publications",
    response_model=ApiResponse[list[ImodigiExportRead]],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_list_publications",
)
async def list_publications(
    request: Request,
    status: str | None = Query(None, description="Filter by status: pending | published | updated | failed"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all Imodigi publication records with optional status filter."""
    rows, total = await list_export_records(db, status=status, page=page, page_size=page_size)
    pages = (total + page_size - 1) // page_size if total else 0
    return ok(
        [ImodigiExportRead.model_validate(r) for r in rows],
        "Exports retrieved",
        request,
        meta=Meta(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post(
    "/publications/reset",
    response_model=ApiResponse[dict],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_reset_publications",
)
async def reset_publications(
    payload: ImodigiResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete Imodigi export records so listings are re-created (POST) on the next export.

    Use this when a property was deleted in Imodigi after being exported — without
    resetting, the system would try to PATCH a non-existent property and fail.

    - Pass ``listing_ids`` to reset specific listings.
    - Pass an **empty list** to reset **all** export records.
    """
    count = await reset_export_records(db, payload.listing_ids)
    scope = f"{len(payload.listing_ids)} listing(s)" if payload.listing_ids else "all listings"
    return ok({"deleted": count}, f"Reset {count} export record(s) for {scope}", request)


@router.delete(
    "/publications/{listing_id}",
    response_model=ApiResponse[dict],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_reset_publication",
)
async def reset_publication(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete the Imodigi export record for a single listing.

    After reset, the next export will send a POST (create) instead of PATCH (update).
    Returns 404 if no export record exists for the listing.
    """
    await reset_export_record(db, listing_id)
    return ok({"deleted": 1}, f"Export record reset for listing {listing_id}", request)


@router.get(
    "/publications/{listing_id}",
    response_model=ApiResponse[ImodigiExportRead],
    responses=ERROR_RESPONSES,
    operation_id="imodigi_get_publication",
)
async def get_publication(
    listing_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the Imodigi publication record for a specific listing."""
    record = await get_export_record(db, listing_id)
    return ok(ImodigiExportRead.model_validate(record), "Export retrieved", request)
