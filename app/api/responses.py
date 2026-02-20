# app/api/responses.py
from typing import Any, Optional
from fastapi import Request
from app.schemas.base_schema import ApiResponse


def ok(
    data: Any,
    message: str,
    request: Optional[Request] = None,
    meta: Optional[dict] = None,
) -> ApiResponse:
    """Wrap a successful response in the standard ApiResponse envelope."""
    return ApiResponse(
        success=True,
        data=data,
        meta=meta,
        message=message,
        errors=None,
        trace_id=getattr(request.state, "trace_id", "") if request else "",
    )