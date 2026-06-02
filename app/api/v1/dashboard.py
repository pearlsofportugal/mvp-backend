"""Dashboard API router.
/api/v1/dashboard
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.schemas.base_schema import ApiResponse
from app.schemas.dashboard_schema import PartnerStatsResponse, WeeklyStats, WeeklyStatsResponse
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get(
    "/partners",
    response_model=ApiResponse[PartnerStatsResponse],
    responses=ERROR_RESPONSES,
    operation_id="partner_stats",
)
async def partner_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PartnerStatsResponse]:
    """Per-partner dashboard: listing counts, price stats, enrichment, Imodigi export, and last scrape job."""
    result = await DashboardService.get_partner_stats(db)
    return ok(result, "Partner stats retrieved successfully", request)

@router.get(
    "/weekly-stats",
    response_model=ApiResponse[WeeklyStatsResponse], # O teu wrapper padrão de respostas
    responses=ERROR_RESPONSES,
    operation_id="weekly_stats"
)
async def weekly_stats(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[WeeklyStatsResponse]:
    """
    Retorna o histórico de crescimento de imóveis agrupado pelas últimas 6 semanas
    para alimentar o gráfico de área do Dashboard.
    """
    
    # ── Exemplo do que a tua lógica de BD/Service deve gerar ──
    # (Substitui este mock pela tua query real do SQLAlchemy quando a fizeres)
    result = await DashboardService.get_weekly_stats(db)

    # Retorna usando o teu padrão de ApiResponse do projeto
    return ok(result, "weekly stats retrieved successfully", request)
