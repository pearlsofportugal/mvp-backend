"""Tests for post-crawl confidence calculation."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from types import SimpleNamespace

from app.crawler.confidence import calculate_confidence
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig
from app.services.scraper_service import _complete_job


def test_calculate_confidence_returns_field_coverage() -> None:
    results = [
        SimpleNamespace(
            price_amount=250000,
            title="Moradia T3",
            area_useful_m2=120.0,
            bedrooms=3,
            district="Lisboa",
            media_assets=[SimpleNamespace(url="https://example.pt/a.jpg")],
        ),
        SimpleNamespace(
            price_amount=None,
            title="Apartamento T2",
            area_useful_m2=None,
            bedrooms=None,
            district="Porto",
            media_assets=[],
        ),
    ]

    scores = calculate_confidence(results)

    assert scores == {
        "price": 0.5,
        "title": 1.0,
        "area": 0.5,
        "rooms": 0.5,
        "location": 1.0,
        "images": 0.5,
    }


@pytest.mark.asyncio
async def test_complete_job_persists_site_confidence_scores(db_session: AsyncSession) -> None:
    job_id = uuid4()
    site = SiteConfig(
        key="test_site",
        name="Test Site",
        base_url="https://example.pt",
        selectors={},
    )
    job = ScrapeJob(
        id=job_id,
        site_key="test_site",
        base_url="https://example.pt",
        start_url="https://example.pt/imoveis",
        max_pages=1,
        status="running",
    )
    listing = Listing(
        source_partner="test_site",
        source_url="https://example.pt/imoveis/1",
        title="Moradia T3",
        price_amount=250000,
        area_useful_m2=120.0,
        bedrooms=3,
        district="Lisboa",
        scrape_job_id=job_id,
    )

    db_session.add_all([site, job, listing])
    await db_session.commit()

    await _complete_job(db_session, str(job_id))

    persisted_site = (
        await db_session.execute(select(SiteConfig).where(SiteConfig.key == "test_site"))
    ).scalar_one()
    persisted_job = (
        await db_session.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    ).scalar_one()

    assert persisted_job.status == "completed"
    assert persisted_site.confidence_scores == {
        "price": 1.0,
        "title": 1.0,
        "area": 1.0,
        "rooms": 1.0,
        "location": 1.0,
        "images": 0.0,
    }
