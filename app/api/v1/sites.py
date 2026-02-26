"""Site Configs API router — CRUD for scraping site configurations.
/api/v1/sites"""
from typing import List

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import DuplicateError, NotFoundError
from app.models.site_config_model import SiteConfig
from app.schemas.base_schema import ApiResponse
from app.schemas.site_config_schema import SiteConfigCreate, SiteConfigRead, SiteConfigUpdate
from app.api.responses import ok
from app.schemas.preview_schema import (
    PreviewListingRequest,
    PreviewListingPageRequest,
    PreviewListingResponse,
    PreviewListingPageResponse,
)
from app.services.preview_service import preview_listing_detail, preview_listing_page
router = APIRouter()

@router.post("/preview/listing", response_model=ApiResponse[PreviewListingResponse])
async def preview_listing_endpoint(payload: PreviewListingRequest, request: Request):
    """Test selectors against a real listing detail page.

    Fetches the URL, runs the parser with the provided selectors, and returns
    field-by-field results showing what was extracted vs what is missing.
    Does NOT save anything to the database.

    Use this before saving a site config to validate your selectors.
    """
    result = await preview_listing_detail(
        url=payload.url,
        selectors=payload.selectors,
        extraction_mode=payload.extraction_mode,
        base_url=payload.base_url,
        image_filter=payload.image_filter,
    )
    return ok(result, "Preview completed", request)


@router.post("/preview/listing-page", response_model=ApiResponse[PreviewListingPageResponse])
async def preview_listing_page_endpoint(payload: PreviewListingPageRequest, request: Request):
    """Test listing link extraction against a real search/listing page.

    Fetches the URL and returns all listing links found + next page URL.
    Use this to validate 'listing_link_selector', 'link_pattern', and 'next_page_selector'.
    Does NOT save anything to the database.
    """
    result = await preview_listing_page(
        url=payload.url,
        selectors=payload.selectors,
        base_url=payload.base_url,
        link_pattern=payload.link_pattern,
    )
    return ok(result, "Listing page preview completed", request)

@router.get("", response_model=ApiResponse[List[SiteConfigRead]])
async def list_sites(
    request: Request,
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(False, description="Include deactivated sites"),
):
    """List all configured scraping sites."""
    query = select(SiteConfig).order_by(SiteConfig.name)
    if not include_inactive:
        query = query.where(SiteConfig.is_active.is_(True))
    result = await db.execute(query)
    sites = result.scalars().all()
    return ok([SiteConfigRead.model_validate(s) for s in sites], "Sites listed successfully", request)


@router.get("/{key}", response_model=ApiResponse[SiteConfigRead])
async def get_site(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Get a site configuration by key."""
    result = await db.execute(select(SiteConfig).where(SiteConfig.key == key))
    site = result.scalar_one_or_none()
    if not site:
        raise NotFoundError(f"Site config '{key}' not found")
    return ok(SiteConfigRead.model_validate(site), "Site retrieved successfully", request)



@router.post("", response_model=ApiResponse[SiteConfigRead], status_code=201)
async def create_site(payload: SiteConfigCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Add a new site configuration.
    
    If a deactivated site with the same key exists, it will be reactivated and updated.
    """
    existing = await db.execute(select(SiteConfig).where(SiteConfig.key == payload.key))
    existing_site = existing.scalar_one_or_none()
    
    if existing_site:
        if existing_site.is_active:
            raise DuplicateError(f"Site config with key '{payload.key}' already exists")
        
        update_data = payload.model_dump()
        for field, value in update_data.items():
            setattr(existing_site, field, value)
        existing_site.is_active = True
        await db.flush()
        return ok(SiteConfigRead.model_validate(existing_site), "Site reactivated successfully", request)

    site = SiteConfig(**payload.model_dump())
    db.add(site)
    await db.flush()
    return ok(SiteConfigRead.model_validate(site), "Site created successfully", request)


@router.patch("/{key}", response_model=ApiResponse[SiteConfigRead])
async def update_site(key: str, payload: SiteConfigUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    """Update a site configuration."""
    result = await db.execute(select(SiteConfig).where(SiteConfig.key == key))
    site = result.scalar_one_or_none()
    if not site:
        raise NotFoundError(f"Site config '{key}' not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(site, field, value)

    await db.flush()
    return ok(SiteConfigRead.model_validate(site), "Site updated successfully", request)


@router.post("/{key}/reactivate", response_model=ApiResponse[SiteConfigRead])
async def reactivate_site(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Reactivate a deactivated site configuration."""
    result = await db.execute(select(SiteConfig).where(SiteConfig.key == key))
    site = result.scalar_one_or_none()
    if not site:
        raise NotFoundError(f"Site config '{key}' not found")
    if site.is_active:
        raise DuplicateError(f"Site config '{key}' is already active")
    
    site.is_active = True
    await db.flush()
    return ok(SiteConfigRead.model_validate(site), "Site reactivated successfully", request)


@router.delete("/{key}", response_model=ApiResponse[None], status_code=200)
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
    result = await db.execute(select(SiteConfig).where(SiteConfig.key == key))
    site = result.scalar_one_or_none()
    if not site:
        raise NotFoundError(f"Site config '{key}' not found")
    
    if permanent:
        await db.delete(site)
    else:
        if not site.is_active:
            raise NotFoundError(f"Site config '{key}' is already deactivated. Use permanent=true to delete permanently.")
        site.is_active = False
    
    await db.flush()
    return ok(None, "Site deleted successfully" if permanent else "Site deactivated successfully", request)

