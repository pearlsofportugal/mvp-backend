"""Test fixtures — async test client, test database, factories."""
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.api.deps import get_db
from app.main import app


TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create tables and yield a test database session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_factory() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Yield an HTTP test client with the test DB injected."""

    async def override_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()



def make_listing_payload(**overrides) -> dict:
    """Create a valid listing creation payload."""
    defaults = {
        "source_partner": "pearls",
        "title": "Test Apartment T2 in Lisbon",
        "listing_type": "sale",
        "property_type": "apartment",
        "typology": "T2",
        "bedrooms": 2,
        "bathrooms": 1,
        "price_amount": 250000.00,
        "price_currency": "EUR",
        "area_useful_m2": 80.0,
        "area_gross_m2": 100.0,
        "district": "Lisboa",
        "county": "Lisboa",
        "parish": "Estrela",
        "has_garage": True,
        "has_elevator": True,
        "has_balcony": False,
        "has_air_conditioning": False,
        "has_pool": False,
        "energy_certificate": "B",
        "source_url": "https://example.com/property/12345",
        "raw_description": "Beautiful apartment in the heart of Lisbon with two bedrooms and a living room.",
    }
    defaults.update(overrides)
    return defaults


def make_site_config_payload(**overrides) -> dict:
    """Create a valid site config creation payload."""
    defaults = {
        "key": "test_site",
        "name": "Test Site",
        "base_url": "https://test.example.com",
        "extraction_mode": "direct",
        "selectors": {
            "listing_link_selector": "a.listing",
            "title_selector": "h1.title",
            "price_selector": ".price",
        },
    }
    defaults.update(overrides)
    return defaults
