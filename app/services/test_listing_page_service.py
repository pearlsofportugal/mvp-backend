"""Service — test a listing/search results page without persisting.

Fetches the page with EthicalScraper, extracts listing links (separated into
matched/rejected), detects the next page URL, and optionally samples thumbnail
images. No DB writes.
"""
import asyncio
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.models.site_config_model import SiteConfig
from app.schemas.site_config_schema import TestListingPageRequest, TestListingPageResponse
from app.services.ethics_service import EthicalScraper
from app.services.parser_service import parse_next_page

logger = get_logger(__name__)


async def run_test_listing_page(
    site: SiteConfig,
    payload: TestListingPageRequest,
) -> TestListingPageResponse:
    """Fetch *payload.url* and report extracted links, thumbnails, and pagination.

    Selectors and patterns fall back to the site's saved configuration when not
    provided in the payload. Never writes to the database.
    """
    url = str(payload.url)
    selectors: dict = site.selectors or {}

    # Payload overrides trump site config —————————————————————————————————
    link_selector: str = selectors.get("listing_link_selector", "a")
    link_pattern: str | None = payload.link_pattern or selectors.get("listing_link_pattern")
    thumbnail_selector: str | None = payload.thumbnail_selector or selectors.get("thumbnail_selector")

    # ── Fetch ─────────────────────────────────────────────────────────────
    scraper = EthicalScraper(user_agent="MVPScraper/1.0 (+test-listing-page)")
    try:
        response = await asyncio.to_thread(scraper.get, url)
    except Exception as exc:
        logger.error("test-listing-page fetch failed for %s: %s", url, exc)
        return TestListingPageResponse(
            url=url,
            success=False,
            links_found=0,
            links_matched=0,
            error=str(exc),
        )
    finally:
        scraper.close()

    if not response:
        return TestListingPageResponse(
            url=url,
            success=False,
            links_found=0,
            links_matched=0,
            error="Failed to fetch page — blocked by robots.txt, rate limit, or HTTP error.",
        )

    html = response.text

    # ── Link extraction (matched vs rejected) ─────────────────────────────
    soup = BeautifulSoup(html, "lxml")
    base_url = site.base_url or url

    matched: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()

    try:
        for a_tag in soup.select(link_selector):
            href = a_tag.get("href")
            if not href:
                continue
            absolute = urljoin(base_url, href)
            if absolute in seen:
                continue
            seen.add(absolute)

            if link_pattern and not re.search(link_pattern, absolute):
                rejected.append(absolute)
            else:
                matched.append(absolute)
    except Exception as exc:
        logger.warning("test-listing-page link extraction error: %s", exc)
        return TestListingPageResponse(
            url=url,
            success=False,
            links_found=0,
            links_matched=0,
            error=f"Link extraction error: {exc}",
        )

    links_found = len(matched) + len(rejected)

    # ── Next page ─────────────────────────────────────────────────────────
    try:
        next_page_url = parse_next_page(html, base_url, selectors)
    except Exception:
        next_page_url = None

    # ── Thumbnail samples ─────────────────────────────────────────────────
    thumbnail_preview: list[str] = []
    if thumbnail_selector:
        try:
            for el in soup.select(thumbnail_selector)[:5]:
                src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                if src:
                    thumbnail_preview.append(urljoin(base_url, src))
        except Exception as exc:
            logger.warning("test-listing-page thumbnail extraction error: %s", exc)

    return TestListingPageResponse(
        url=url,
        success=True,
        links_found=links_found,
        links_matched=len(matched),
        sample_matched=matched[:5],
        sample_rejected=rejected[:5],
        thumbnail_preview=thumbnail_preview,
        next_page_url=next_page_url,
    )
