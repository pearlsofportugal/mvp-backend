"""A renamed ad (new slug, same id) must retire its old URL instead of staying active as a duplicate."""
import pytest
from sqlalchemy import select

from app.core.lifecycle import STATUS_ACTIVE, STATUS_REMOVED
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.services import scraper_service

BASE = "https://www.mysquare.pt/pt/imoveis/"
PATTERN = r"_(\d+)/?$"


def _url(slug: str, ad_id: int) -> str:
    return f"{BASE}{slug}_{ad_id}/"


def _job() -> ScrapeJob:
    job = ScrapeJob(site_key="mysquare", attempt_count=0)
    job.mark_running()
    return job


async def _add(db, url: str, partner: str = "mysquare", status: str = STATUS_ACTIVE) -> Listing:
    listing = Listing(source_partner=partner, source_url=url, title="x", status=status)
    db.add(listing)
    await db.commit()
    return listing


async def _status(db, listing: Listing) -> str:
    await db.refresh(listing)
    return listing.status


class TestRemoveSupersededDuplicates:
    async def test_old_slug_is_removed_when_the_same_id_is_discovered_under_a_new_url(self, db_session):
        old = await _add(db_session, _url("apartamento-t3", 1188938))
        new = await _add(db_session, _url("apartamento-t3-com-108-m-a-venda-em-canidelo-gaia", 1188938))

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {new.source_url}, PATTERN
        )
        await db_session.commit()

        assert removed == 1
        assert await _status(db_session, old) == STATUS_REMOVED
        assert old.removed_at is not None
        assert await _status(db_session, new) == STATUS_ACTIVE

    async def test_listing_whose_id_was_not_discovered_is_left_alone(self, db_session):
        gone = await _add(db_session, _url("terreno", 1191523))
        other = await _add(db_session, _url("moradia", 1))

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {other.source_url}, PATTERN
        )

        assert removed == 0
        assert await _status(db_session, gone) == STATUS_ACTIVE  # left for the regular missing-listing rule

    async def test_a_discovered_url_is_never_removed_even_if_its_id_is_shared(self, db_session):
        a = await _add(db_session, _url("a", 7))
        b = await _add(db_session, _url("b", 7))

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {a.source_url, b.source_url}, PATTERN
        )

        assert removed == 0
        assert await _status(db_session, a) == STATUS_ACTIVE
        assert await _status(db_session, b) == STATUS_ACTIVE

    async def test_ids_are_matched_exactly_not_by_suffix(self, db_session):
        short = await _add(db_session, _url("a", 51141))
        discovered = _url("b", 1251141)

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {discovered}, PATTERN
        )

        assert removed == 0
        assert await _status(db_session, short) == STATUS_ACTIVE

    async def test_other_partners_are_untouched(self, db_session):
        mine = await _add(db_session, _url("old", 5))
        theirs = await _add(db_session, _url("old", 5).replace("mysquare", "other"), partner="other")

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {_url("new", 5)}, PATTERN
        )
        await db_session.commit()

        assert removed == 1
        assert await _status(db_session, mine) == STATUS_REMOVED
        assert await _status(db_session, theirs) == STATUS_ACTIVE

    async def test_the_old_twin_is_kept_when_the_new_url_failed_this_run(self, db_session):
        old = await _add(db_session, _url("old", 9))
        new_url = _url("new", 9)
        job = _job()
        job.add_url("failed", new_url)

        removed = await scraper_service._remove_superseded_duplicates(db_session, job, "mysquare", {new_url}, PATTERN)

        assert removed == 0
        assert await _status(db_session, old) == STATUS_ACTIVE

    @pytest.mark.parametrize("pattern", [None, "", "(", r"_\d+/?$"])  # missing, empty, invalid, no capture group
    async def test_without_a_usable_pattern_nothing_changes(self, db_session, pattern):
        old = await _add(db_session, _url("old", 3))

        removed = await scraper_service._remove_superseded_duplicates(
            db_session, _job(), "mysquare", {_url("new", 3)}, pattern
        )

        assert removed == 0
        assert await _status(db_session, old) == STATUS_ACTIVE


class TestRemoveMissingListingsIntegration:
    async def test_duplicates_do_not_count_against_the_safety_brake(self, db_session):
        # 10 discovered properties, each with a stale old-slug twin (50% of active rows are "missing"),
        # plus 1 genuinely gone property. Without the cleanup the 40% brake blocks everything.
        discovered = set()
        for i in range(1, 11):
            new = await _add(db_session, _url("new", i))
            await _add(db_session, _url("old", i))
            discovered.add(new.source_url)
        gone = await _add(db_session, _url("gone", 999))

        removed = await scraper_service._remove_missing_listings(
            db_session, _job(), "mysquare", discovered, listing_id_pattern=PATTERN
        )
        await db_session.commit()

        assert removed == 11  # 10 duplicates + the one gone from the site
        active = (await db_session.execute(
            select(Listing.source_url).where(Listing.source_partner == "mysquare", Listing.status == STATUS_ACTIVE)
        )).scalars().all()
        assert set(active) == discovered
        assert await _status(db_session, gone) == STATUS_REMOVED

    async def test_without_the_pattern_the_brake_still_blocks_as_before(self, db_session):
        discovered = set()
        for i in range(1, 11):
            new = await _add(db_session, _url("new", i))
            await _add(db_session, _url("old", i))
            discovered.add(new.source_url)

        removed = await scraper_service._remove_missing_listings(db_session, _job(), "mysquare", discovered)

        assert removed == 0  # 50% > 40%: other sites keep the existing behaviour

    async def test_the_brake_still_protects_when_a_real_crawl_failure_remains(self, db_session):
        discovered = set()
        for i in range(1, 11):
            discovered.add((await _add(db_session, _url("new", i))).source_url)
        for i in range(100, 110):  # 10 stale rows with ids absent from the crawl: not duplicates
            await _add(db_session, _url("stale", i))

        removed = await scraper_service._remove_missing_listings(
            db_session, _job(), "mysquare", discovered, listing_id_pattern=PATTERN
        )

        assert removed == 0


class TestMysquareConfig:
    def test_script_ships_a_pattern_that_extracts_the_stable_id(self):
        import importlib.util
        import re
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "scripts" / "fix_site_configs.py"
        spec = importlib.util.spec_from_file_location("fix_site_configs", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)

        patch = script.build_patch({"key": "mysquare", "selectors": {}})
        pattern = re.compile(patch["selectors"]["listing_id_pattern"])
        assert pattern.search(_url("apartamento-t3", 1188938)).group(1) == "1188938"
        assert pattern.search(f"{BASE}moradia-t5_1189001").group(1) == "1189001"  # no trailing slash
        assert "listing_id_pattern" not in script.build_patch({"key": "habinedita", "selectors": {"title_selector": "h1"}}).get("selectors", {})
