"""Site Configs API router — CRUD for scraping site configurations.
/api/v1/sites
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.responses import ERROR_RESPONSES, ok
from app.crawler.selector_suggester import preview_selector, suggest_selectors
from app.schemas.base_schema import ApiResponse
from app.schemas.site_config_schema import (
    SiteConfigCreate,
    SiteConfigPreviewRequest,
    SiteConfigPreviewResponse,
    SiteConfigRead,
    SiteConfigSuggestRequest,
    SiteConfigSuggestResponse,
    SiteConfigUpdate,
)
from app.services.site_config_service import SiteConfigService

router = APIRouter()


@router.post(
    "/preview/selector-suggestions",
    response_model=ApiResponse[SiteConfigSuggestResponse],
    responses=ERROR_RESPONSES,
    operation_id="suggest_site_selectors",
)
async def suggest_site_selectors(payload: SiteConfigSuggestRequest, request: Request):
    """Suggest likely selectors for a listing detail page before saving a site config."""
    result = await suggest_selectors(str(payload.url))
    return ok(SiteConfigSuggestResponse.model_validate(result), "Selector suggestions generated", request)


@router.post(
    "/preview/selector",
    response_model=ApiResponse[SiteConfigPreviewResponse],
    responses=ERROR_RESPONSES,
    operation_id="preview_site_selector",
)
async def preview_site_selector(payload: SiteConfigPreviewRequest, request: Request):
    """Preview live matches for a single selector against a detail page."""
    result = await preview_selector(str(payload.url), payload.selector)
    return ok(SiteConfigPreviewResponse.model_validate(result), "Selector preview completed", request)


# ---------------------------------------------------------------------------
# Site config CRUD
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ApiResponse[list[SiteConfigRead]],
    responses=ERROR_RESPONSES,
    operation_id="list_sites",
)
async def list_sites(
    request: Request,
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(False, description="Include deactivated sites"),
):
    """List all configured scraping sites."""
    sites = await SiteConfigService.get_all(db, include_inactive=include_inactive)
    return ok([SiteConfigRead.model_validate(s) for s in sites], "Sites listed successfully", request)


@router.post(
    "",
    response_model=ApiResponse[SiteConfigRead],
    status_code=201,
    responses=ERROR_RESPONSES,
    operation_id="create_site",
)
async def create_site(
    payload: SiteConfigCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a new site configuration.

    If a deactivated site with the same key exists, it will be reactivated and updated.
    """
    site, message = await SiteConfigService.create(db, payload)
    return ok(SiteConfigRead.model_validate(site), message, request)


@router.get(
    "/{key}",
    response_model=ApiResponse[SiteConfigRead],
    responses=ERROR_RESPONSES,
    operation_id="get_site",
)
async def get_site(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Get a site configuration by key."""
    site = await SiteConfigService.get_by_key(db, key)
    return ok(SiteConfigRead.model_validate(site), "Site retrieved successfully", request)


@router.patch(
    "/{key}",
    response_model=ApiResponse[SiteConfigRead],
    responses=ERROR_RESPONSES,
    operation_id="update_site",
)
async def update_site(
    key: str,
    payload: SiteConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update a site configuration."""
    site = await SiteConfigService.update(db, key, payload)
    return ok(SiteConfigRead.model_validate(site), "Site updated successfully", request)


@router.delete(
    "/{key}",
    response_model=ApiResponse[None],
    status_code=200,
    responses=ERROR_RESPONSES,
    operation_id="delete_site",
)
async def delete_site(
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    permanent: bool = Query(False, description="Permanently delete instead of soft delete"),
):
    """Delete a site configuration.

    By default, performs a soft delete (deactivates the site).
    Use permanent=true to permanently delete the record.
    """
    message = await SiteConfigService.delete(db, key, permanent=permanent)
    return ok(None, message, request)
