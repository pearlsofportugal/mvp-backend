"""Dashboard API router.
/api/v1/dashboard
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.schemas.base_schema import ApiResponse
from app.schemas.dashboard_schema import PartnerStatsResponse
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
