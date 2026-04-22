"""Sitemap fetching utility — parses XML sitemaps and returns property URLs."""
import re

import defusedxml.ElementTree as ElementTree

from app.core.logging import get_logger
from app.services.ethics_service import EthicalScraper

logger = get_logger(__name__)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def fetch_sitemap_urls(
    sitemap_url: str,
    link_pattern: str | None,
    scraper: EthicalScraper,
) -> list[str]:
    """Fetch a sitemap XML and return all <loc> URLs matching link_pattern.

    Supports sitemap index files (recursively fetches sub-sitemaps).
    Returns an empty list if the sitemap cannot be fetched.
    """
    response = scraper.get(sitemap_url)
    if not response:
        logger.warning("Could not fetch sitemap: %s", sitemap_url)
        return []

    try:
        root = ElementTree.fromstring(response.text)
    except ElementTree.ParseError as exc:
        logger.error("Failed to parse sitemap XML from %s: %s", sitemap_url, exc)
        return []

    # Sitemap index — recurse into sub-sitemaps
    sitemaps = root.findall("sm:sitemap", _SITEMAP_NS) or root.findall("sitemap")
    if sitemaps:
        all_urls: list[str] = []
        for sitemap_el in sitemaps:
            loc_el = sitemap_el.find("sm:loc", _SITEMAP_NS)
            if loc_el is None:
                loc_el = sitemap_el.find("loc")
            if loc_el is not None and loc_el.text:
                child_urls = fetch_sitemap_urls(loc_el.text.strip(), link_pattern, scraper)
                all_urls.extend(child_urls)
        return all_urls

    # Regular sitemap — collect <loc> entries
    url_els = root.findall("sm:url", _SITEMAP_NS) or root.findall("url")
    urls: list[str] = []
    for url_el in url_els:
        loc_el = url_el.find("sm:loc", _SITEMAP_NS)
        if loc_el is None:
            loc_el = url_el.find("loc")
        if loc_el is None or not loc_el.text:
            continue
        href = loc_el.text.strip()
        if link_pattern and not re.search(link_pattern, href):
            continue
        urls.append(href)

    logger.info("Sitemap %s — %d URLs matched pattern '%s'", sitemap_url, len(urls), link_pattern)
    return urls
