# app/api/responses.py
from typing import Any

from fastapi import Request

from app.schemas.base_schema import ApiResponse, Meta

# Standard 422 responses override — use `responses=ERROR_RESPONSES` on every
# route decorator so Pydantic validation errors follow the ApiResponse envelope
# instead of FastAPI's default HTTPValidationError shape.
ERROR_RESPONSES: dict = {
    401: {"model": ApiResponse, "description": "Authentication required or invalid API key."},
    404: {"model": ApiResponse, "description": "Resource not found."},
    422: {"model": ApiResponse},
}


def ok(
    data: Any,
    message: str,
    request: Request | None = None,
    meta: Meta | dict | None = None,
) -> ApiResponse:
    """Wrap a successful response in the standard ApiResponse envelope.

    ``meta`` accepts either a ``Meta`` instance or a plain dict, which Pydantic
    will coerce automatically — keeping router call-sites concise.
    """
    resolved_meta: Meta | None = None
    if isinstance(meta, dict):
        resolved_meta = Meta(**meta)
    elif isinstance(meta, Meta):
        resolved_meta = meta

    return ApiResponse(
        success=True,
        data=data,
        meta=resolved_meta,
        message=message,
        errors=None,
        trace_id=getattr(request.state, "trace_id", None) if request else None,
    )