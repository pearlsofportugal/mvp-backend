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


class TestPropertyTypeAndConditionAreLanguageAgnostic:
    """English-language partners (e.g. bpaproperty) must score the same as
    Portuguese ones — both regexes used to only recognize PT terms, which
    silently scored 0% for any English site regardless of correct data."""

    def test_english_property_types_are_recognized(self) -> None:
        for property_type in ("Townhouse", "Villa", "Apartment", "Plot", "Garage"):
            result = SimpleNamespace(
                price_amount=1, title="x", area_useful_m2=1, bedrooms=1, district="x",
                media_assets=[], property_type=property_type, typology=None,
                condition=None, business_type="sale", area_land_m2=None,
            )
            scores = calculate_confidence([result])
            assert scores["property_type"] == 1.0, property_type

    def test_english_conditions_are_recognized(self) -> None:
        for condition in ("Used", "New", "Renovated"):
            result = SimpleNamespace(
                price_amount=1, title="x", area_useful_m2=1, bedrooms=1, district="x",
                media_assets=[], property_type=None, typology=None,
                condition=condition, business_type="sale", area_land_m2=None,
            )
            scores = calculate_confidence([result])
            assert scores["condition"] == 1.0, condition


class TestNotApplicableFields:
    """A partner can opt structurally-inapplicable fields (e.g. typology for
    an English site with no T-code convention) out of scoring entirely,
    rather than being permanently penalized for something it could never
    have populated."""

    def _make_result(self, **overrides):
        defaults = dict(
            price_amount=1, title="x", area_useful_m2=1, bedrooms=1, district="x",
            media_assets=[], property_type="Villa", typology=None,
            condition=None, business_type="sale", area_land_m2=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_excluded_field_is_absent_from_result_not_scored_zero(self) -> None:
        scores = calculate_confidence([self._make_result()], not_applicable_fields=["typology"])

        assert "typology" not in scores
        assert "condition" in scores  # unrelated fields still scored normally

    def test_excluding_a_field_raises_the_average_of_the_rest(self) -> None:
        # typology=None and condition=None would both score 0% normally.
        # Excluding typology removes it from the denominator entirely.
        without_exclusion = calculate_confidence([self._make_result()])
        with_exclusion = calculate_confidence([self._make_result()], not_applicable_fields=["typology"])

        assert without_exclusion["typology"] == 0.0
        assert "typology" not in with_exclusion
        assert sum(with_exclusion.values()) / len(with_exclusion) > sum(without_exclusion.values()) / len(without_exclusion)

    def test_no_exclusions_behaves_exactly_as_before(self) -> None:
        result = self._make_result()
        assert calculate_confidence([result]) == calculate_confidence([result], not_applicable_fields=None)
        assert calculate_confidence([result]) == calculate_confidence([result], not_applicable_fields=[])


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
        "property_type": 0.0,
        "typology": 0.0,
        "condition": 0.0,
        "business_type": 0.0,
        "land_area": 0.0,
    }


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

    await _complete_job(db_session, job)

    persisted_site = (
        await db_session.execute(select(SiteConfig).where(SiteConfig.key == "test_site"))
    ).scalar_one()
    persisted_job = (
        await db_session.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    ).scalar_one()

    assert persisted_job.status == "completed"
    # _update_site_confidence_scores always adds a `_meta` key alongside the
    # per-field scores (job_id, sample_count, updated_at) — stripped out in
    # SiteConfigRead and exposed separately as `confidence_meta`.
    scores = dict(persisted_site.confidence_scores)
    meta = scores.pop("_meta")
    assert meta["job_id"] == str(job_id)
    assert meta["sample_count"] == 1
    assert scores == {
        "price": 1.0,
        "title": 1.0,
        "area": 1.0,
        "rooms": 1.0,
        "location": 1.0,
        "images": 0.0,
        "property_type": 0.0,
        "typology": 0.0,
        "condition": 0.0,
        "business_type": 0.0,
        "land_area": 0.0,
    }
