"""test-scrape / test-listing-page must run with what the real scraper runs with."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas import site_config_schema as schemas
from app.services.test_listing_page_service import run_test_listing_page
from app.services.test_scrape_service import run_test_scrape
from app.utils.site_selectors import effective_selectors


def _site(**kw):
    base = dict(
        key="realkey", base_url="https://www.example.pt", selectors={}, extraction_mode="direct",
        use_js_render=False, link_pattern=None, image_filter=None, image_exclude_filter=None, request_headers=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _ethical(html: str) -> MagicMock:
    scraper = MagicMock()
    scraper.get.return_value = SimpleNamespace(text=html)
    return scraper


_DETAIL = """
<html><body><h1 class="t">Apartamento T2</h1>
<img class="g" src="https://imgs.centralimo.pt/1/properties/a.jpg">
<img class="g" src="https://cdn.example.pt/banner.jpg">
</body></html>
"""
_LISTING = """
<html><body>
<a class="card" href="/imovel/casa-1/1">a</a>
<a class="card" href="/imovel/casa-2/2">b</a>
<a class="card" href="/blog/novidade">c</a>
</body></html>
"""


def test_effective_selectors_merges_site_level_filters():
    out = effective_selectors({"a": 1}, "/imovel/", r"imgs\.x", "logo")
    assert out == {"a": 1, "listing_link_pattern": "/imovel/", "image_filter": r"imgs\.x", "image_exclude_filter": "logo"}
    assert effective_selectors(None) == {}


def test_test_scrape_applies_the_site_image_filter():
    site = _site(selectors={"images_selector": "img.g", "title_selector": "h1.t"}, image_filter=r"imgs\.centralimo\.pt/.+/properties/")
    with patch("app.services.test_scrape_service.EthicalScraper", return_value=_ethical(_DETAIL)):
        result = asyncio.run(run_test_scrape(site, "https://www.example.pt/imovel/1"))
    assert result.raw["images"] == ["https://imgs.centralimo.pt/1/properties/a.jpg"]


def test_test_scrape_without_filter_keeps_every_image():
    site = _site(selectors={"images_selector": "img.g", "title_selector": "h1.t"})
    with patch("app.services.test_scrape_service.EthicalScraper", return_value=_ethical(_DETAIL)):
        result = asyncio.run(run_test_scrape(site, "https://www.example.pt/imovel/1"))
    assert len(result.raw["images"]) == 2


def test_test_listing_page_uses_the_site_link_pattern():
    site = _site(selectors={"listing_link_selector": "a.card"}, link_pattern=r"/imovel/[\w-]+/\d+")
    with patch("app.services.test_listing_page_service.EthicalScraper", return_value=_ethical(_LISTING)):
        result = asyncio.run(run_test_listing_page(site, schemas.TestListingPageRequest(url="https://www.example.pt/imoveis")))
    assert result.links_matched == 2
    assert result.sample_rejected == ["https://www.example.pt/blog/novidade"]


def test_test_listing_page_renders_js_sites_with_playwright():
    site = _site(selectors={"listing_link_selector": "a.card"}, use_js_render=True)
    browser = MagicMock()
    browser.get_html = AsyncMock(return_value=_LISTING)
    browser.close = AsyncMock()
    with patch("app.services.test_listing_page_service.PlaywrightScraper", return_value=browser) as pw, \
         patch("app.services.test_listing_page_service.EthicalScraper") as static:
        result = asyncio.run(run_test_listing_page(site, schemas.TestListingPageRequest(url="https://www.example.pt/imoveis")))
    assert result.links_found == 3
    static.assert_not_called()
    assert pw.call_args.kwargs["content_ready_selector"] == "a.card"
    browser.close.assert_awaited()


def test_double_escaped_filter_is_repaired_when_the_job_runs():
    from app.utils.site_selectors import repair_regex

    broken = "imgs" + "\\" * 2 + ".centralimo" + "\\" * 2 + ".pt.+/properties/"
    assert repair_regex(broken) == r"imgs\.centralimo\.pt.+/properties/"
    assert repair_regex(r"already\.fine") == r"already\.fine"
    assert repair_regex(None) is None
    assert effective_selectors({}, image_filter=broken)["image_filter"] == r"imgs\.centralimo\.pt.+/properties/"


def test_invalid_pattern_does_not_break_the_merge_but_is_rejected_on_save():
    import pytest
    from pydantic import ValidationError

    assert effective_selectors({}, link_pattern="(")["listing_link_pattern"] == "("
    with pytest.raises(ValidationError):
        schemas.SiteConfigUpdate(image_filter="(")
    broken = "x" + "\\" * 2 + ".y"
    assert schemas.SiteConfigUpdate(image_filter=broken).image_filter == r"x\.y"


def test_sitemap_fetch_for_js_sites_presents_the_browser_user_agent():
    """mysquare answers 403 to the bot UA on robots.txt and the sitemap, which left the
    whole site with zero URLs; the sitemap fetch must look like the page fetches do."""
    from app.services import scraper_service
    from app.services.ethics_service import EthicalScraper
    from app.services.playwright_scraper import BROWSER_USER_AGENT, PlaywrightScraper

    captured: dict = {}

    class FakeEthical(EthicalScraper):
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    browser = PlaywrightScraper(extra_headers={"Accept-Language": "pt-PT"})
    with patch.object(scraper_service, "EthicalScraper", FakeEthical), \
         patch.object(scraper_service, "fetch_sitemap_urls", return_value=[]), \
         patch.object(scraper_service, "_complete_job", new=AsyncMock()):
        asyncio.run(scraper_service._run_sitemap_scrape(
            db=MagicMock(), job=MagicMock(), job_id="1", site_key="mysquare",
            sitemap_url="https://www.mysquare.pt/sitemap.xml", full_selectors={},
            extraction_mode="direct", link_pattern=None, scraper=browser,
        ))
    assert captured["user_agent"] == BROWSER_USER_AGENT
    assert captured["extra_headers"] == {"Accept-Language": "pt-PT"}
