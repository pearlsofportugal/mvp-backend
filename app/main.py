"""FastAPI application factory and startup configuration."""
from contextlib import asynccontextmanager
from uuid import uuid4
from app.schemas.base_schema import ApiResponse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.exceptions import (
    AppException,
    DuplicateError,
    JobAlreadyRunningError,
    NotFoundError,
)
from app.core.logging import setup_logging, get_logger
from app.api.v1.listings import router as listings_router
from app.api.v1.scrape_jobs import router as jobs_router
from app.api.v1.sites import router as sites_router
from app.api.v1.ai_enrichment import router as ai_enrichment_router
from app.api.v1.export import router as export_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    setup_logging()
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Real Estate Scraper Backend API — scrape, enrich, and serve property listings.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @application.middleware("http")
    async def add_trace_id(request:Request,call_next):
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", None)
        logger.exception("Unhandled exception [trace_id=%s]", trace_id, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=ApiResponse(
                success=False,
                data=None,
                message="Erro interno",
                errors=["Internal server error"],
                trace_id=trace_id,
            ).model_dump(),
        )

    # Exception handlers
    @application.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        trace_id = getattr(request.state, "trace_id", None)
        return JSONResponse(
            status_code=404,
            content=ApiResponse(
                success=False,
                data=None,
                message=exc.message,
                errors=None,
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(DuplicateError)
    async def duplicate_handler(request: Request, exc: DuplicateError):
        trace_id = getattr(request.state, "trace_id", None)
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                data=None,
                message=exc.message,
                errors=None,
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(JobAlreadyRunningError)
    async def job_running_handler(request: Request, exc: JobAlreadyRunningError):
        trace_id = getattr(request.state, "trace_id", None)
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                data=None,
                message=exc.message,
                errors=None,
                trace_id=trace_id,
            ).model_dump(),
        )

    @application.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        trace_id = getattr(request.state, "trace_id", None)
        return JSONResponse(
            status_code=500,
            content=ApiResponse(
                success=False,
                data=None,
                message=exc.message,
                errors=None,
                trace_id=trace_id,
            ).model_dump(),
        )

    # Include routers

    application.include_router(listings_router, prefix="/api/v1/listings", tags=["Listings"])
    application.include_router(jobs_router, prefix="/api/v1/jobs", tags=["Scrape Jobs"])
    application.include_router(sites_router, prefix="/api/v1/sites", tags=["Site Configs"])
    application.include_router(ai_enrichment_router, prefix="/api/v1/enrichment/ai", tags=["Enrichment AI"])
    application.include_router(export_router, prefix="/api/v1/export", tags=["Export"])
    # Healthcheck
    @application.get("/health", response_model=ApiResponse[dict], tags=["Health"])
    async def health(request: Request):
        """Health check endpoint — verifies DB connectivity."""
        from sqlalchemy import text
        from app.database import async_session_factory
        from app.api.responses import ok

        db_status = "ok"
        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as e:
            db_status = f"error: {str(e)}"
        return ok(
            {"status": "healthy" if db_status == "ok" else "unhealthy", "version":settings.app_version, "database":db_status},
            "Health check completed",
            request,
        )


    return application


app = create_app()

