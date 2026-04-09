import math
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, NotFoundError
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.repositories.listings_repository import ListingRepository, ListingStatsData
from app.schemas.base_schema import Meta
from app.schemas.listing_schema import (
    DuplicateEntry,
    DuplicatesResponse,
    ListingCreate,
    ListingListRead,
    ListingStats,
    ListingUpdate,
    PaginatedResponse,
)
from app.schemas.listing_search_schema import ListingSearchItem, ListingSearchResponse

SORT_FIELDS = {
    "price": Listing.price_amount,
    "area": Listing.area_useful_m2,
    "bedrooms": Listing.bedrooms,
    "created_at": Listing.created_at,
    "updated_at": Listing.updated_at,
    "district": Listing.district,
    "title": Listing.title,
}

class ListingService:

    @staticmethod
    async def get_listing_by_id(db: AsyncSession, listing_id: UUID) -> Listing:
        listing = await ListingRepository.get_listing_by_id(db, listing_id)
        if not listing:
            raise NotFoundError(f"Listing {listing_id} not found")
        return listing

    @staticmethod
    async def get_all_listings(
        db: AsyncSession,
        filters: dict,
        sort_by: str,
        sort_order: str,
        page: int,
        page_size: int,
    ) -> tuple[PaginatedResponse, Meta]:
        sort_column = SORT_FIELDS.get(sort_by, Listing.created_at)
        listings = await ListingRepository.get_all_listings(
            db, filters, sort_column, sort_order, page, page_size
        )
        total = await ListingRepository.count_listings(db, filters)
        pages = math.ceil(total / page_size) if total else 0
        meta = Meta(page=page, page_size=page_size, total=total, pages=pages)
        return PaginatedResponse(items=[ListingListRead.model_validate(l) for l in listings]), meta

    @staticmethod
    async def search_listings(
        db: AsyncSession,
        q: str | None,
        source_partner: str | None,
        is_enriched: bool | None,
        page: int,
        page_size: int,
    ) -> tuple[ListingSearchResponse, Meta]:
        listings, total = await ListingRepository.search_listings(
            db, q, source_partner, is_enriched, page, page_size
        )
        items: list[ListingSearchItem] = []
        for listing in listings:
            thumbnail_url: str | None = None
            if listing.media_assets:
                first = min(listing.media_assets, key=lambda m: m.position or 999)
                thumbnail_url = first.url
            items.append(
                ListingSearchItem(
                    id=listing.id,
                    source_partner=listing.source_partner,
                    title=((listing.enriched_translations or {}).get("en") or {}).get("title") or listing.title,
                    property_type=listing.property_type,
                    typology=listing.typology,
                    bedrooms=listing.bedrooms,
                    area_useful_m2=listing.area_useful_m2,
                    district=listing.district,
                    county=listing.county,
                    price_amount=listing.price_amount,
                    price_currency=listing.price_currency,
                    listing_type=listing.listing_type,
                    thumbnail_url=thumbnail_url,
                    is_enriched=bool(listing.enriched_translations),
                )
            )
        pages = math.ceil(total / page_size) if total > 0 else 0
        return ListingSearchResponse(items=items), Meta(page=page, page_size=page_size, total=total, pages=pages)

    @staticmethod
    async def get_stats(
        db: AsyncSession,
        source_partner: str | None,
        scrape_job_id: UUID | None,
    ) -> ListingStats:
        stats: ListingStatsData = await ListingRepository.get_stats(db, source_partner, scrape_job_id)
        return ListingStats(
            total_listings=stats.total,
            avg_price=float(stats.avg_price) if stats.avg_price else None,
            min_price=float(stats.min_price) if stats.min_price else None,
            max_price=float(stats.max_price) if stats.max_price else None,
            avg_area=float(stats.avg_area) if stats.avg_area else None,
            by_district=stats.by_district,
            by_property_type=stats.by_property_type,
            by_source_partner=stats.by_source_partner,
            by_typology=stats.by_typology,
        )

    @staticmethod
    async def get_duplicates(
        db: AsyncSession,
        page: int,
        page_size: int,
    ) -> tuple[DuplicatesResponse, Meta]:
        groups, total = await ListingRepository.get_duplicate_groups(db, page, page_size)
        entries = [DuplicateEntry(source_url=url, count=count) for url, count in groups]
        pages = math.ceil(total / page_size) if total > 0 else 0
        return (
            DuplicatesResponse(duplicates=entries, total=total),
            Meta(page=page, page_size=page_size, total=total, pages=pages),
        )

    @staticmethod
    async def create_listing(db: AsyncSession, payload: ListingCreate) -> Listing:
        if payload.source_url:
            existing = await ListingRepository.get_by_source_url(db, payload.source_url)
            if existing:
                raise DuplicateError(f"Listing with source_url '{payload.source_url}' already exists")
        data = payload.model_dump(exclude={"media_assets"})
        listing = Listing(**data)
        media_assets = [MediaAsset(**asset_data.model_dump()) for asset_data in payload.media_assets]
        return await ListingRepository.create_listing(db, listing, media_assets)

    @staticmethod
    async def update_listing(db: AsyncSession, listing_id: UUID, payload: ListingUpdate) -> Listing:
        listing = await ListingRepository.get_listing_by_id(db, listing_id)
        if not listing:
            raise NotFoundError(f"Listing {listing_id} not found")
        update_data = payload.model_dump(exclude_unset=True)
        price_history = None
        if "price_amount" in update_data and update_data["price_amount"] is not None:
            if listing.price_amount is not None and listing.price_amount != update_data["price_amount"]:
                price_history = PriceHistory(
                    listing_id=listing.id,
                    price_amount=listing.price_amount,
                    price_currency=listing.price_currency or "EUR",
                )
        for field, value in update_data.items():
            setattr(listing, field, value)
        return await ListingRepository.update_listing(db, listing, price_history)

    @staticmethod
    async def delete_listing(db: AsyncSession, listing_id: UUID) -> None:
        listing = await ListingRepository.get_listing_by_id(db, listing_id)
        if not listing:
            raise NotFoundError(f"Listing {listing_id} not found")
        await ListingRepository.delete_listing(db, listing)