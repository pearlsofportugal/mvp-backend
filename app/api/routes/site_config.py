"""Legacy aliases for site-config tooling routes.

Canonical endpoints now live under /api/v1/sites/preview/*.
"""

from fastapi import APIRouter, Request

from app.api.responses import ERROR_RESPONSES
from app.api.v1.sites import preview_site_selector, suggest_site_selectors
from app.schemas.base_schema import ApiResponse
from app.schemas.site_config_schema import (
    SiteConfigPreviewRequest,
    SiteConfigPreviewResponse,
    SiteConfigSuggestRequest,
    SiteConfigSuggestResponse,
)

router = APIRouter(prefix="/site-config")


@router.post(
    "/suggest",
    response_model=ApiResponse[SiteConfigSuggestResponse],
    responses=ERROR_RESPONSES,
    operation_id="suggest_site_config_selectors",
    deprecated=True,
    include_in_schema=False,
)
async def suggest_site_config_selectors(payload: SiteConfigSuggestRequest, request: Request):
    """Legacy alias for POST /api/v1/sites/preview/selector-suggestions."""
    return await suggest_site_selectors(payload, request)


@router.post(
    "/preview",
    response_model=ApiResponse[SiteConfigPreviewResponse],
    responses=ERROR_RESPONSES,
    operation_id="preview_site_config_selector",
    deprecated=True,
    include_in_schema=False,
)
async def preview_site_config_selector(payload: SiteConfigPreviewRequest, request: Request):
    """Legacy alias for POST /api/v1/sites/preview/selector."""
    return await preview_site_selector(payload, request)
