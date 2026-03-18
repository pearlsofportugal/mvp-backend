"""Base Pydantic schemas shared across the entire API.

Defines the uniform API envelope (ApiResponse), pagination metadata (Meta),
error detail (ErrorDetail), and system health (SystemHealth).
"""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Structured error detail for client consumption."""

    code: str = Field(..., description="Machine-readable error code (e.g. 'NOT_FOUND', 'VALIDATION_ERROR').")
    message: str = Field(..., description="Human-readable error description.")
    field: str | None = Field(None, description="Name of the field that caused the error, if applicable.")


class Meta(BaseModel):
    """Pagination metadata attached to list responses."""

    page: int | None = Field(None, ge=1, description="Current page number (1-indexed).")
    page_size: int | None = Field(None, ge=1, description="Number of items per page.")
    total: int | None = Field(None, ge=0, description="Total number of items across all pages.")
    pages: int | None = Field(None, ge=0, description="Total number of pages.")


class ApiResponse(BaseModel, Generic[T]):
    """Uniform API envelope for all responses.

    Every endpoint returns this shape so the frontend can rely on a single
    discriminated union: ``success=True`` → read ``data``,
    ``success=False`` → read ``errors``.
    """

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the request succeeded.")
    data: T | None = Field(None, description="Response payload; present when success=True.")
    meta: Meta | None = Field(None, description="Pagination metadata for list responses.")
    message: str = Field("", description="Optional human-readable message.")
    errors: list[ErrorDetail] = Field(default_factory=list, description="Error details; present when success=False.")
    trace_id: str = Field("", description="Correlation ID for distributed tracing.")


class SystemHealth(BaseModel):
    """Response schema for GET /health."""

    status: Literal["healthy", "unhealthy"] = Field(..., description="Overall system health status.")
    version: str = Field(..., description="Application version string.")
    database: Literal["ok", "degraded", "unreachable"] = Field(
        "ok",
        description="Database connectivity status.",
    )