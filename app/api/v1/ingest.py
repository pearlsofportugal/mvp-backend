"""Ingest API router — extract one property listing from any URL.
/api/v1/ingest
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.schemas.base_schema import ApiResponse
from app.schemas.ingest_schema import IngestRequest, IngestResponse
from app.services.ingest_service import ingest_listing

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[IngestResponse],
    responses=ERROR_RESPONSES,
    operation_id="ingest_listing",
)
async def ingest(
    payload: IngestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Extract a single property listing from any URL.

    If the host matches an active site configuration, the precise CSS-selector
    pipeline is used. Otherwise a generic layered pipeline runs (structured data
    → selector suggester → regex heuristics → optional LLM fallback).

    Nothing is written to the database — the caller persists the result itself.
    The `completeness.missing` list tells the caller which fields to ask a human
    to fill in.
    """
    result = await ingest_listing(db, str(payload.url))
    return ok(result, "Ingest completed" if result.success else result.error, request)
