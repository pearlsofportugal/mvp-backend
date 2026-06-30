"""API dependencies — database session, authentication, and common utilities.

FIX (prioridade alta): Adicionada autenticação por API key.

  ANTES: todos os endpoints eram públicos — qualquer pessoa com acesso à rede
         podia lançar jobs, apagar listings, ou chamar a Gemini API sem limite.

  DEPOIS: header X-API-Key obrigatório em todos os endpoints (exceto /health e /docs).
          A key é configurada via variável de ambiente API_KEY no .env.

  SETUP:
    1. Adicionar ao .env:  API_KEY=a-tua-chave-secreta-aqui
    2. Adicionar ao config.py:  api_key: str = ""
    3. Incluir o router de auth em main.py (opcional — para /auth/verify)
    4. Nos pedidos HTTP: header  X-API-Key: a-tua-chave-secreta-aqui

  ANGULAR: o ApiKeyInterceptor (ficheiro separado) injeta o header automaticamente.

  SEGURANÇA: para produção real considera OAuth2/JWT. Esta implementação é adequada
  para um MVP interno ou tool privada.
"""
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.database import async_session_factory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Database session dependency (inalterado)
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for request scope."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# API Key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API key configured through API_KEY in the environment",
)



async def verify_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> str:
    """Validate the X-API-Key header."""
    if not settings.api_key:
        if settings.dev_auth_bypass and not settings.is_production:
            logger.warning("DEV_AUTH_BYPASS is active — all requests accepted without authentication")
            return "dev-bypass"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_KEY is not configured on the server.",
        )

    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


RequireApiKey = Depends(verify_api_key)


# ── Shared listing filter query parameters ──────────────────────────────────────────────────

def listing_filter_params(
    district: str | None = Query(None),
    county: str | None = Query(None),
    parish: str | None = Query(None),
    property_type: str | None = Query(None),
    typology: str | None = Query(None),
    business_type: str | None = Query(None, pattern="^(sale|rent)$"),
    source_partner: str | None = Query(None),
    scrape_job_id: UUID | None = Query(None),
    price_min: Decimal | None = Query(None),
    price_max: Decimal | None = Query(None),
    area_min: float | None = Query(None),
    area_max: float | None = Query(None),
    bedrooms_min: int | None = Query(None),
    bedrooms_max: int | None = Query(None),
    bathrooms_min: int | None = Query(None),
    bathrooms_max: int | None = Query(None),
    energy_certificate: str | None = Query(None),
    has_garage: bool | None = Query(None),
    has_pool: bool | None = Query(None),
    has_elevator: bool | None = Query(None),
    has_balcony: bool | None = Query(None),
    has_air_conditioning: bool | None = Query(None),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    search: str | None = Query(None),
) -> dict:
    """Shared listing filter parameters used by /listings and /export endpoints."""
    return dict(
        district=district, county=county, parish=parish,
        property_type=property_type, typology=typology, business_type=business_type,
        source_partner=source_partner, scrape_job_id=scrape_job_id,
        price_min=price_min, price_max=price_max,
        area_min=area_min, area_max=area_max,
        bedrooms_min=bedrooms_min, bedrooms_max=bedrooms_max,
        bathrooms_min=bathrooms_min, bathrooms_max=bathrooms_max,
        energy_certificate=energy_certificate,
        has_garage=has_garage, has_pool=has_pool, has_elevator=has_elevator,
        has_balcony=has_balcony, has_air_conditioning=has_air_conditioning,
        created_after=created_after, created_before=created_before,
        search=search,
    )