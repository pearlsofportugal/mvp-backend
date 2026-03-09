"""FastAPI application factory and startup configuration."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import RequireApiKey
from app.api.responses import ok
from app.api.v1.ai_enrichment import router as ai_enrichment_router
from app.api.v1.export import router as export_router
from app.api.v1.listings import router as listings_router
from app.api.v1.scrape_jobs import router as jobs_router
from app.api.v1.sites import router as sites_router
from app.config import settings
from app.core.exceptions import (
    AppException,
    DuplicateError,
    JobAlreadyRunningError,
    NotFoundError,
)
from app.core.logging import get_logger, setup_logging
from app.schemas.base_schema import ApiResponse, ErrorDetail, SystemHealth

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    setup_logging()
    logger.info(
        "Starting %s v%s [env=%s]",
        settings.app_name,
        settings.app_version,
        settings.app_env,
    )

    if not settings.api_key:
        logger.warning("API_KEY is not configured. Protected routes will reject requests.")

    yield

    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Real Estate Scraper Backend API — scrape, enrich, and serve property listings.",
        docs_url="/docs" if settings.effective_docs_enabled else None,
        redoc_url="/redoc" if settings.effective_docs_enabled else None,
        lifespan=lifespan,
    )

    # ── Middleware ─────────────────────────────────────────────────────────

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def add_trace_id(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    # ── Exception handlers ─────────────────────────────────────────────────

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", "")
        logger.exception("Unhandled exception [trace_id=%s]", trace_id, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=ApiResponse(
                success=False,
                message="Internal server error",
                errors=[ErrorDetail(code="INTERNAL_ERROR", message="An unexpected error occurred.")],
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        trace_id = getattr(request.state, "trace_id", "")
        errors = [
            ErrorDetail(
                code="VALIDATION_ERROR",
                message=err["msg"],
                field=str(err["loc"][-1]) if err.get("loc") else None,
            )
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=ApiResponse(
                success=False,
                message="Validation failed",
                errors=errors,
                trace_id=trace_id,
            ).model_dump(),
        )

    # FIX: AppException base handler — catches any AppException not handled below
    @application.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=400,
            content=ApiResponse(
                success=False,
                message=str(exc),
                errors=[ErrorDetail(code="APP_ERROR", message=str(exc))],
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=404,
            content=ApiResponse(
                success=False,
                message=str(exc),
                errors=[ErrorDetail(code="NOT_FOUND", message=str(exc))],
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(DuplicateError)
    async def duplicate_handler(request: Request, exc: DuplicateError):
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                message=str(exc),
                errors=[ErrorDetail(code="DUPLICATE", message=str(exc))],
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(JobAlreadyRunningError)
    async def job_running_handler(request: Request, exc: JobAlreadyRunningError):
        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                message=str(exc),
                errors=[ErrorDetail(code="JOB_ALREADY_RUNNING", message=str(exc))],
                trace_id=trace_id,
            ).model_dump(),
        )

    # ── Routers ────────────────────────────────────────────────────────────

    auth_dependencies = [RequireApiKey]

    application.include_router(
        listings_router,
        prefix="/api/v1/listings",
        tags=["listings"],
        dependencies=auth_dependencies,
    )
    application.include_router(
        jobs_router,
        prefix="/api/v1/jobs",
        tags=["jobs"],
        dependencies=auth_dependencies,
    )
    application.include_router(
        sites_router,
        prefix="/api/v1/sites",
        tags=["sites"],
        dependencies=auth_dependencies,
    )
    application.include_router(
        ai_enrichment_router,
        prefix="/api/v1/enrichment/ai",
        tags=["enrichment"],
        dependencies=auth_dependencies,
    )
    application.include_router(
        export_router,
        prefix="/api/v1/export",
        tags=["export"],
        dependencies=auth_dependencies,
    )

    # ── Public endpoints ───────────────────────────────────────────────────

    @application.get("/health", tags=["system"], response_model=ApiResponse[SystemHealth])
    async def health_check(request: Request):
        """System health check. Public — no authentication required."""
        from sqlalchemy import text

        from app.database import async_session_factory

        db_status = "ok"
        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Health check DB error: %s", exc)
            db_status = "unreachable"

        is_healthy = db_status == "ok"
        return ok(
            SystemHealth(
                status="healthy" if is_healthy else "unhealthy",
                version=settings.app_version,
                database=db_status,
            ),
            "Health check completed",
            request,
        )

    return application


app = create_app()