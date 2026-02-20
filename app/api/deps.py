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
from typing import AsyncGenerator, Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory


# ---------------------------------------------------------------------------
# Database session dependency (inalterado)
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for request scope."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
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
    auto_error=False,  # False para retornar 401 customizado em vez de 403
    description="API key de autenticação. Configurada via API_KEY no .env",
)


async def verify_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> str:
    """Valida o header X-API-Key.

    Usa comparação constant-time (secrets.compare_digest) para evitar
    timing attacks — mesmo que improvável num MVP, é boa prática.

    Raises:
        HTTPException 401: se a key estiver ausente ou incorreta.
        HTTPException 500: se API_KEY não estiver configurada no servidor.
    """
    if not settings.api_key:
        # Servidor mal configurado — não expor detalhes ao cliente
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Servidor não configurado corretamente (API_KEY em falta).",
        )

    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente. Usa o header X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


# Shorthand para usar como dependency nos routers
RequireApiKey = Depends(verify_api_key)