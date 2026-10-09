"""Checks that SQLite cannot make: the PostgreSQL upsert branch and the real migrated schema.

Skipped unless ``TEST_POSTGRES_URL`` points at a database that has been migrated with
``alembic upgrade head`` (the tests deliberately use the schema the migrations built, not
``create_all``), e.g.:

    docker run -d --rm --name pg-verify -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=verify -p 55432:5432 postgres:18-alpine
    DATABASE_URL=postgresql+asyncpg://postgres:verify@localhost:55432/verify \\
    DATABASE_URL_SYNC=postgresql://postgres:verify@localhost:55432/verify \\
    python -m alembic upgrade head
    TEST_POSTGRES_URL=postgresql+asyncpg://postgres:verify@localhost:55432/verify python -m pytest tests/test_postgres_integration.py

The tests TRUNCATE listings: never point this at a database that holds data you want to keep.
"""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.repositories.listings_repository import ListingRepository
from app.schemas.property_schema import Address, MediaAsset as MediaSchema, Money, PropertySchema
from app.services import scraper_service

PG_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL not set")

URL = "https://agency.pt/imovel/1"


@pytest_asyncio.fixture
async def pg():
    engine = create_async_engine(PG_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE listings CASCADE"))
    yield factory
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE listings CASCADE"))
    await engine.dispose()


def _schema(**kw) -> PropertySchema:
    base = dict(
        source_partner="pearls", source_url=URL, partner_id="REF-1", title="Apartamento T2 no Porto",
        property_type="Apartamento", typology="T2", typology_extra="+1", business_type="sale",
        price=Money(amount=250000, currency="EUR"), area_useful_m2=80.0,
        address=Address(region="Porto", city="Porto", area="Paranhos"),
        district_code="13", county_code="1312", parish_code="131211",
        latitude=41.1579, longitude=-8.6291, location_precision="exact",
        media=[MediaSchema(url="https://cdn.pt/1.jpg", type="photo", position=0, width=1600, height=1200),
               MediaSchema(url="https://cdn.pt/2.jpg", type="photo", position=1)],
        descriptions={"raw": "Bonito apartamento.", "pt": "Bonito apartamento.", "clean": "Bonito apartamento."},
    )
    base.update(kw)
    return PropertySchema(**base)


async def _persist(factory, schema) -> bool:
    async with factory() as db:
        assert db.get_bind().dialect.name == "postgresql"      # the branch under test
        is_new = await scraper_service._persist_listing(db, str(uuid4()), schema, "pearls")
        await db.commit()
        return is_new


async def _row(factory) -> Listing:
    async with factory() as db:
        return (await db.execute(select(Listing).where(Listing.source_url == URL))).scalar_one()


async def test_insert_then_rescrape_keeps_updated_at(pg):
    assert await _persist(pg, _schema()) is True
    first = await _row(pg)
    assert first.content_hash and first.status == "active" and first.first_seen_at and first.last_seen_at
    assert (first.district_code, first.county_code, first.parish_code) == ("13", "1312", "131211")
    assert (first.typology, first.typology_extra, first.location_precision) == ("T2", "+1", "exact")

    assert await _persist(pg, _schema()) is False
    second = await _row(pg)
    assert second.updated_at == first.updated_at               # the Core UPDATE really bypasses onupdate
    assert second.content_hash == first.content_hash
    assert second.last_seen_at > first.last_seen_at


async def test_real_change_moves_updated_at_and_records_price_history(pg):
    await _persist(pg, _schema())
    before = await _row(pg)
    await _persist(pg, _schema(price=Money(amount=240000, currency="EUR")))
    after = await _row(pg)
    assert after.updated_at > before.updated_at and float(after.price_amount) == 240000
    async with pg() as db:
        assert [float(h.price_amount) for h in (await db.execute(select(PriceHistory))).scalars()] == [250000]


async def test_gallery_is_stored_ordered_and_left_alone_when_unchanged(pg):
    await _persist(pg, _schema())
    async with pg() as db:
        ids = [m.id for m in (await db.execute(select(MediaAsset).order_by(MediaAsset.position))).scalars()]
        assert len(ids) == 2
    await _persist(pg, _schema())
    async with pg() as db:
        rows = (await db.execute(select(MediaAsset).order_by(MediaAsset.position))).scalars().all()
        assert [m.id for m in rows] == ids
        assert (rows[0].width, rows[0].height, rows[1].width) == (1600, 1200, None)


async def test_concurrent_first_scrapes_of_the_same_url_create_one_row(pg):
    """Two workers meeting the same new URL: ON CONFLICT DO NOTHING + reload, no duplicate, no crash."""
    results = await asyncio.gather(*[_persist(pg, _schema()) for _ in range(4)], return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert results.count(True) == 1
    async with pg() as db:
        assert len((await db.execute(select(Listing))).scalars().all()) == 1
        assert len((await db.execute(select(MediaAsset))).scalars().all()) == 2


async def test_removed_listing_is_reactivated_by_a_scrape(pg):
    await _persist(pg, _schema())
    async with pg() as db:
        listing = (await db.execute(select(Listing))).scalar_one()
        assert await ListingRepository.mark_removed(db, listing) is True
        await db.commit()
    removed = await _row(pg)
    assert removed.status == "removed" and removed.removed_at is not None

    await _persist(pg, _schema())
    back = await _row(pg)
    assert back.status == "active" and back.removed_at is None and back.updated_at > removed.updated_at


async def test_missing_listings_are_soft_deleted_per_partner(pg):
    async with pg() as db:
        db.add_all([
            Listing(source_partner="pearls", source_url="https://x.pt/keep", title="a"),
            Listing(source_partner="pearls", source_url="https://x.pt/gone", title="b"),
            Listing(source_partner="other", source_url="https://y.pt/z", title="c"),
        ])
        await db.commit()
    async with pg() as db:
        assert await ListingRepository.mark_missing_removed(db, "pearls", {"https://x.pt/keep"}) == 1
        await db.commit()
    async with pg() as db:
        status = {l.source_url: l.status for l in (await db.execute(select(Listing))).scalars()}
    assert status == {"https://x.pt/keep": "active", "https://x.pt/gone": "removed", "https://y.pt/z": "active"}


async def test_list_filters_run_on_postgres(pg):
    await _persist(pg, _schema())
    async with pg() as db:
        listing = (await db.execute(select(Listing))).scalar_one()
        await ListingRepository.mark_removed(db, listing)
        await db.commit()
    async with pg() as db:
        count = lambda f: ListingRepository.count_listings(db, f)
        assert await count({}) == 0                              # default hides removed
        assert await count({"status": "removed"}) == 1
        assert await count({"status": "all", "county_code": "1312"}) == 1
        assert await count({"status": "all", "district_code": "11"}) == 0
        assert await count({"status": "all", "updated_after": listing.updated_at.replace(year=2000)}) == 1
