"""GET /api/v2/listings: the contract feed, paginated by cursor and resumable with `updated_since`."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.contract_v2 import SCHEMA_VERSION, partner_profile, to_contract
from app.contract_v2.cursor import InvalidCursor, decode_cursor, encode_cursor
from app.models.listing_model import Listing
from app.models.site_config_model import SiteConfig
from app.repositories.listings_repository import ListingRepository

router = APIRouter()

MAX_LIMIT = 200


async def partner_profiles(db: AsyncSession) -> dict[str, dict]:
    sites = (await db.execute(select(SiteConfig))).scalars().all()
    return {site.key: partner_profile(site) for site in sites}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("", summary="Listings in contract v2 format")
async def list_listings(
    updated_since: datetime | None = Query(None, description="Only listings whose public content changed at or after this instant (ISO 8601)."),
    cursor: str | None = Query(None, description="`next_cursor` of the previous page."),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    partner: str | None = Query(None, description="Only this source (partner_key)."),
    status: Literal["active", "removed", "all"] = Query("all", description="`removed` rows stay in the feed so the plugin can archive them."),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    try:
        position = decode_cursor(cursor) if cursor else None
    except InvalidCursor:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    listings, total, has_more = await ListingRepository.list_for_contract(
        db,
        updated_since=updated_since,
        cursor=position,
        limit=limit,
        partner=partner,
        status=None if status == "all" else status,
    )
    partners = await partner_profiles(db)
    items = [to_contract(listing, partners.get(listing.source_partner)) for listing in listings]
    next_cursor = encode_cursor(listings[-1].updated_at, listings[-1].id) if has_more and listings else None
    return JSONResponse({
        "schema_version": SCHEMA_VERSION,
        "items": items,
        "next_cursor": next_cursor,
        "total": total,
        "generated_at": _now_iso(),
    })


@router.get("/{listing_id}", summary="One listing in contract v2 format")
async def get_listing(listing_id: UUID, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    listing = (
        await db.execute(
            select(Listing)
            .where(Listing.id == listing_id)
            .options(selectinload(Listing.media_assets), selectinload(Listing.price_history))
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    partners = await partner_profiles(db)
    return JSONResponse(to_contract(listing, partners.get(listing.source_partner)))
