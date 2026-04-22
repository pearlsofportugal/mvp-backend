from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, asc, desc, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.utils._filters import apply_listing_filters


@dataclass
class ListingStatsData:
    total: int
    avg_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    avg_area: float | None
    by_district: dict[str, int]
    by_property_type: dict[str, int]
    by_source_partner: dict[str, int]
    by_typology: dict[str, int]

class ListingRepository:

    @staticmethod
    async def get_by_source_url(db: AsyncSession, source_url: str) -> Listing | None:
        result = await db.execute(select(Listing).where(Listing.source_url == source_url))
        return result.scalar_one_or_none()

    @staticmethod
    async def count_listings(db: AsyncSession, filters: dict) -> int:
        query = apply_listing_filters(select(func.count(Listing.id)), filters)
        return (await db.execute(query)).scalar_one()

    @staticmethod
    async def get_listing_by_id(db: AsyncSession, listing_id: UUID) -> Listing | None:
        result = await db.execute(
            select(Listing)
            .where(Listing.id == listing_id)
            .options(
                selectinload(Listing.media_assets),
                selectinload(Listing.price_history),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all_listings(
        db: AsyncSession,
        filters: dict,
        sort_column: InstrumentedAttribute,
        sort_order: str,
        page: int,
        page_size: int,
    ) -> tuple[list[Listing], int]:
        # COUNT(*) OVER() calcula o total sem query separada
        total_count = func.count().over().label("total_count")
    
        query = apply_listing_filters(
            select(Listing, total_count), filters
        )
        query = query.order_by(desc(sort_column) if sort_order == "desc" else asc(sort_column))
        query = query.offset((page - 1) * page_size).limit(page_size)
    
        rows = (await db.execute(query)).all()
    
        if not rows:
            return [], 0
    
        listings = [row[0] for row in rows]  # objeto Listing
        total = rows[0][1]                   # total_count da primeira linha
    
        return listings, total

    @staticmethod
    async def get_listings_for_export(db: AsyncSession, filters: dict, limit: int | None = None) -> list[Listing]:
        query = apply_listing_filters(select(Listing), filters).order_by(Listing.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return (await db.execute(query)).scalars().all()

    @staticmethod
    async def search_listings(
        db: AsyncSession,
        q: str | None,
        source_partner: str | None,
        is_enriched: bool | None,
        is_exported_to_imodigi: bool | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Listing], int]:
        stmt = select(Listing)
        if q:
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Listing.title.ilike(pattern),
                    Listing.district.ilike(pattern),
                    Listing.county.ilike(pattern),
                    Listing.source_partner.ilike(pattern),
                )
            )
        if source_partner:
            stmt = stmt.where(Listing.source_partner == source_partner)
        if is_enriched is True:
            stmt = stmt.where(Listing.enriched_translations.isnot(None))
        elif is_enriched is False:
            stmt = stmt.where(Listing.enriched_translations.is_(None))
        _imodigi_exists = exists(
            select(ImodigiExport.id).where(
                ImodigiExport.listing_id == Listing.id,
                ImodigiExport.status.in_(["published", "updated"]),
            ).correlate(Listing)
        )
        if is_exported_to_imodigi is True:
            stmt = stmt.where(_imodigi_exists)
        elif is_exported_to_imodigi is False:
            stmt = stmt.where(~_imodigi_exists)

        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        stmt = (
            stmt
            .options(selectinload(Listing.media_assets))
            .order_by(Listing.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return (await db.execute(stmt)).scalars().all(), total

    @staticmethod
    async def get_stats(
        db: AsyncSession,
        source_partner: str | None,
        scrape_job_id: UUID | None,
    ) -> ListingStatsData:
        base_filter = []
        if source_partner:
            base_filter.append(Listing.source_partner == source_partner)
        if scrape_job_id:
            base_filter.append(Listing.scrape_job_id == scrape_job_id)
        where_clause = and_(*base_filter) if base_filter else True

        total, avg_price, min_price, max_price, avg_area = (await db.execute(
            select(
                func.count(Listing.id),
                func.avg(Listing.price_amount),
                func.min(Listing.price_amount),
                func.max(Listing.price_amount),
                func.avg(Listing.area_useful_m2),
            ).where(where_clause)
        )).one()

        by_district = {r[0]: r[1] for r in (await db.execute(
            select(Listing.district, func.count(Listing.id))
            .where(where_clause).where(Listing.district.isnot(None))
            .group_by(Listing.district)
        )).all()}

        by_property_type = {r[0]: r[1] for r in (await db.execute(
            select(Listing.property_type, func.count(Listing.id))
            .where(where_clause).where(Listing.property_type.isnot(None))
            .group_by(Listing.property_type)
        )).all()}

        by_source_partner = {r[0]: r[1] for r in (await db.execute(
            select(Listing.source_partner, func.count(Listing.id))
            .where(where_clause).group_by(Listing.source_partner)
        )).all()}

        by_typology = {r[0]: r[1] for r in (await db.execute(
            select(Listing.typology, func.count(Listing.id))
            .where(where_clause).where(Listing.typology.isnot(None))
            .group_by(Listing.typology)
        )).all()}

        return ListingStatsData(
            total=total or 0,
            avg_price=avg_price,
            min_price=min_price,
            max_price=max_price,
            avg_area=avg_area,
            by_district=by_district,
            by_property_type=by_property_type,
            by_source_partner=by_source_partner,
            by_typology=by_typology,
        )

    @staticmethod
    async def get_duplicate_groups(
        db: AsyncSession,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[str, int]], int]:
        count_sub = (
            select(Listing.source_url)
            .where(Listing.source_url.isnot(None))
            .group_by(Listing.source_url)
            .having(func.count(Listing.id) > 1)
            .subquery()
        )
        total = (await db.execute(select(func.count()).select_from(count_sub))).scalar_one()
        rows = (await db.execute(
            select(Listing.source_url, func.count(Listing.id).label("count"))
            .where(Listing.source_url.isnot(None))
            .group_by(Listing.source_url)
            .having(func.count(Listing.id) > 1)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all()
        return [(r[0], r[1]) for r in rows], total

    @staticmethod
    async def create_listing(db: AsyncSession, listing: Listing, media_assets: list[MediaAsset]) -> Listing:
        db.add(listing)
        await db.flush()
        for asset in media_assets:
            asset.listing_id = listing.id
            db.add(asset)
        await db.commit()
        # Re-query with selectinload — required since lazy="raise" on relationships
        result = await ListingRepository.get_listing_by_id(db, listing.id)
        return result  # type: ignore[return-value]  # always non-None immediately after create

    @staticmethod
    async def update_listing(db: AsyncSession, listing: Listing, price_history: PriceHistory | None = None) -> Listing:
        if price_history:
            db.add(price_history)
        await db.commit()
        # Re-query with selectinload — required since lazy="raise" on relationships
        result = await ListingRepository.get_listing_by_id(db, listing.id)
        return result  # type: ignore[return-value]  # always non-None for an existing listing

    @staticmethod
    async def delete_listing(db: AsyncSession, listing: Listing) -> None:
        await db.delete(listing)
        await db.commit()