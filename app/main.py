"""FastAPI application factory and startup configuration.

FIX (prioridade alta): Autenticação aplicada globalmente via router dependencies.
  A estratégia usada é adicionar `dependencies=[RequireApiKey]` a cada router,
  em vez de um middleware global, para manter /health e /docs públicos
  (necessários para Docker healthchecks e desenvolvimento).

MELHORIAS v2:
  - init_parser_cache() chamada no startup para carregar os field mappings da DB
    antes do primeiro job de scraping arrancar.
"""
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas.base_schema import ApiResponse
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
from app.api.deps import RequireApiKey
from app.api.responses import ok

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    setup_logging()
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    if not settings.api_key:
        logger.warning(
            "API_KEY não configurada — endpoints desprotegidos. "
            "Define API_KEY no .env antes de ir a produção."
        )

    # Inicializar cache do parser com os field mappings da DB.
    # Garante que o cache está pronto antes do primeiro job de scraping arrancar,
    # evitando que o primeiro job use sempre os defaults hardcoded.
    try:
        from app.services.parser_service import init_parser_cache
        await init_parser_cache()
        logger.info("Parser field mapping cache initialized")
    except Exception as e:
        logger.warning(
            "Parser cache initialization failed: %s. "
            "Parser will use default field mappings until first successful DB load.",
            str(e),
        )

    # Inicializar cache do mapper com os currency mappings da DB.
    try:
        from app.services.mapper_service import init_mapper_cache
        await init_mapper_cache()
        logger.info("Mapper currency cache initialized")
    except Exception as e:
        logger.warning(
            "Mapper cache initialization failed: %s. "
            "Mapper will use default currency mappings until first successful DB load.",
            str(e),
        )

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

    @application.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(
            status_code=404,
            content=ApiResponse(
                success=False,
                data=None,
                message=str(exc),
                errors=[str(exc)],
                trace_id=getattr(request.state, "trace_id", None),
            ).model_dump(),
        )

    @application.exception_handler(DuplicateError)
    async def duplicate_handler(request: Request, exc: DuplicateError):
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                data=None,
                message=str(exc),
                errors=[str(exc)],
                trace_id=getattr(request.state, "trace_id", None),
            ).model_dump(),
        )

    @application.exception_handler(JobAlreadyRunningError)
    async def job_running_handler(request: Request, exc: JobAlreadyRunningError):
        return JSONResponse(
            status_code=409,
            content=ApiResponse(
                success=False,
                data=None,
                message=str(exc),
                errors=[str(exc)],
                trace_id=getattr(request.state, "trace_id", None),
            ).model_dump(),
        )

    # Todos os routers protegidos com RequireApiKey.
    # /health fica público — necessário para Docker healthchecks e monitorização.
    # /docs e /redoc ficam públicos — para desenvolvimento local.
    _auth = [RequireApiKey]

    application.include_router(listings_router, prefix="/api/v1/listings", tags=["listings"], dependencies=_auth)
    application.include_router(jobs_router, prefix="/api/v1/jobs", tags=["jobs"], dependencies=_auth)
    application.include_router(sites_router, prefix="/api/v1/sites", tags=["sites"], dependencies=_auth)
    application.include_router(ai_enrichment_router, prefix="/api/v1/enrichment/ai", tags=["enrichment"], dependencies=_auth)
    application.include_router(export_router, prefix="/api/v1/export", tags=["export"], dependencies=_auth)

    @application.get("/health", tags=["system"])
    async def health_check(request: Request):
        from sqlalchemy import text
        from app.database import async_session_factory

        db_status = "ok"
        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as e:
            db_status = f"error: {str(e)}"

        return ok(
            {
                "status": "healthy" if db_status == "ok" else "unhealthy",
                "version": settings.app_version,
                "database": db_status,
            },
            "Health check completed",
            request,
        )

    return application


app = create_app()