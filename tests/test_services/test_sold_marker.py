"""Per-site ``sold_marker_selector``: listings flagged sold/rented by a badge element are ignored."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.lifecycle import STATUS_ACTIVE, STATUS_REMOVED
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.services import scraper_service
from app.services.parser_service import parse_listing_page

URL = "https://www.mysquare.pt/pt/imoveis/apartamento-t2_1251141/"

# MySquare uses ONE class (lbl_vendido) for every badge; only the text tells them apart (verified against
# the live sitemap: Vendido, Arrendado and Reservado all render as <span class="lbl_vendido ...">).
MARKER = ".faixa .lbl_vendido:-soup-contains('Vendido'), .faixa .lbl_vendido:-soup-contains('Arrendado')"


def _page(faixa: str = "") -> str:
    return f"""
    <html><head><title>Apartamento T2 | MySquare</title></head><body>
      <h1 class="titulo">Apartamento T2 em Gaia</h1>
      <div class="faixa">{faixa}</div>
      <div id="x_div_preco_1"><span class="val">Sob Consulta</span></div>
    </body></html>
    """


SOLD = '<span id="a_lbl_vendido_1" class="lbl_vendido bg-primary lbl_vendido_1">Vendido</span>'
RENTED = '<span id="a_lbl_vendido_1" class="lbl_vendido bg-primary lbl_vendido_1">Arrendado</span>'
RESERVED = '<span id="a_lbl_vendido_1" class="lbl_vendido bg-primary lbl_vendido_1">Reservado</span>'
SELECTORS = {"title_selector": "h1.titulo", "sold_marker_selector": MARKER}


class TestParserMarker:
    def test_sold_badge_flags_the_listing(self):
        raw = parse_listing_page(_page(SOLD), URL, SELECTORS)
        assert raw["is_sold"] is True
        assert raw["sold_reason"] == "marker"

    def test_rented_badge_flags_the_listing(self):
        raw = parse_listing_page(_page(RENTED), URL, SELECTORS)
        assert raw["is_sold"] is True
        assert raw["sold_reason"] == "marker"

    def test_reserved_badge_is_not_treated_as_sold(self):
        # Reserved listings may come back to the market, so the MySquare marker leaves them alone.
        raw = parse_listing_page(_page(RESERVED), URL, SELECTORS)
        assert raw["is_sold"] is False

    def test_page_without_badge_is_a_normal_listing(self):
        raw = parse_listing_page(_page(""), URL, SELECTORS)
        assert raw["is_sold"] is False
        assert "sold_reason" not in raw

    def test_badge_outside_the_marker_scope_is_not_matched(self):
        elsewhere = f"<html><body><h1 class='titulo'>Apartamento T2</h1><div class='rodape'>{SOLD}</div></body></html>"
        assert parse_listing_page(elsewhere, URL, SELECTORS)["is_sold"] is False

    def test_without_the_selector_other_sites_behave_as_before(self):
        # Same page, same badge, but the site has no sold_marker_selector: nothing changes.
        raw = parse_listing_page(_page(SOLD), URL, {"title_selector": "h1.titulo"})
        assert raw["is_sold"] is False

    @pytest.mark.parametrize("bad", ["", None, 123, "div[", ">>"])
    def test_empty_or_invalid_selector_never_raises_and_never_flags(self, bad):
        raw = parse_listing_page(_page(SOLD), URL, {"title_selector": "h1.titulo", "sold_marker_selector": bad})
        assert raw["is_sold"] is False

    def test_the_keyword_detection_still_works_independently(self):
        page = "<html><body><h1 class='titulo'>Apartamento T2 Vendido</h1></body></html>"
        raw = parse_listing_page(page, URL, {"title_selector": "h1.titulo"})
        assert raw["is_sold"] is True
        assert raw["sold_reason"] == "keyword"


class TestMysquareConfig:
    """scripts/fix_site_configs.py must ship a selector that does what the tests above rely on."""

    @staticmethod
    def _script():
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "scripts" / "fix_site_configs.py"
        spec = importlib.util.spec_from_file_location("fix_site_configs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_patch_adds_the_marker_for_mysquare_only(self):
        script = self._script()
        mysquare = script.build_patch({"key": "mysquare", "selectors": {}})
        assert mysquare["selectors"]["sold_marker_selector"] == script.MYSQUARE_SOLD_MARKER
        other = script.build_patch({"key": "habinedita", "selectors": {"title_selector": "h1"}})
        assert "selectors" not in other  # untouched sites get no selector changes

    def test_imoantlantico_image_filter_matches_production(self):
        # Production already has the wider filter (crmhcpro.pt/uploads); the script must not revert it.
        script = self._script()
        prod = {
            "key": "imoantlantico",
            **{k: v for k, v in script.IMOATLANTICO.items() if k != "selectors"},
            "selectors": script.IMOATLANTICO["selectors"],
            "image_filter": r"(?:multimedia\.hcpro\.pt/hcpro|crmhcpro\.pt/uploads)/\d+/properties/",
        }
        assert "image_filter" not in script.build_patch(prod)

    @pytest.mark.parametrize(("badge", "expected"), [(SOLD, True), (RENTED, True), (RESERVED, False), ("", False)])
    def test_shipped_selector_matches_the_real_badges(self, badge, expected):
        marker = self._script().MYSQUARE_SOLD_MARKER
        raw = parse_listing_page(_page(badge), URL, {"title_selector": "h1.titulo", "sold_marker_selector": marker})
        assert raw["is_sold"] is expected


def _job() -> ScrapeJob:
    job = ScrapeJob(site_key="mysquare", attempt_count=0)  # transient: column defaults only apply on INSERT
    job.mark_running()
    return job


def _run(html: str, existing: Listing | None = None):
    """Run ``_process_listing_url`` for one page with the DB layer stubbed out."""
    job = _job()
    db = AsyncMock()
    scraper = SimpleNamespace(last_status=200)
    sold_repo = SimpleNamespace(
        is_recently_confirmed_sold=AsyncMock(return_value=False),
        mark_sold=AsyncMock(),
        unmark_sold=AsyncMock(),
    )
    persist = AsyncMock(return_value=True)
    patches = [
        patch.object(scraper_service, "_fetch_html", AsyncMock(return_value=html)),
        patch.object(scraper_service, "SoldListingRepository", sold_repo),
        patch.object(scraper_service.ListingRepository, "get_by_source_url", AsyncMock(return_value=existing)),
        patch.object(scraper_service, "_persist_listing", persist),
        patch.object(scraper_service, "normalize_partner_payload", lambda raw, key: SimpleNamespace(raw=raw)),
        patch.object(scraper_service, "is_junk_listing", lambda schema: False),
        patch.object(scraper_service, "missing_critical_schema_fields", lambda schema: []),
    ]
    return job, db, scraper, sold_repo, persist, patches


async def _process(job, db, scraper, patches):
    for p in patches:
        p.start()
    try:
        return await scraper_service._process_listing_url(db, job, "00000000-0000-0000-0000-000000000001",
                                                          "mysquare", scraper, URL, SELECTORS, "direct")
    finally:
        for p in patches:
            p.stop()


class TestScraperSkipsSold:
    async def test_sold_listing_is_skipped_counted_and_not_persisted_or_an_error(self):
        job, db, scraper, sold_repo, persist, patches = _run(_page(SOLD))
        result = await _process(job, db, scraper, patches)

        assert result == (False, False, False, False)  # not scraped, not an error, not a warning
        assert job.progress["skipped_sold"] == 1
        persist.assert_not_awaited()
        sold_repo.mark_sold.assert_awaited_once()
        assert any(URL == entry.get("url") for entry in job.logs["info"])
        assert not job.logs["errors"]

    async def test_existing_listing_becomes_removed_when_it_turns_up_sold(self):
        existing = Listing(source_partner="mysquare", source_url=URL, title="T2", status=STATUS_ACTIVE)
        job, db, scraper, _, persist, patches = _run(_page(SOLD), existing=existing)
        await _process(job, db, scraper, patches)

        assert existing.status == STATUS_REMOVED
        assert existing.removed_at is not None
        persist.assert_not_awaited()

    async def test_listing_without_badge_is_imported_normally(self):
        job, db, scraper, sold_repo, persist, patches = _run(_page(""))
        result = await _process(job, db, scraper, patches)

        assert result == (True, False, False, True)
        assert job.progress["skipped_sold"] == 0
        persist.assert_awaited_once()
        sold_repo.unmark_sold.assert_awaited_once()

    async def test_skipped_sold_accumulates_across_listings(self):
        job = _job()
        for _ in range(3):
            _, db, scraper, _, _, patches = _run(_page(SOLD))
            await _process(job, db, scraper, patches)
        assert job.progress["skipped_sold"] == 3
