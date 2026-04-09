"""Service — run a dry-run scrape on a single listing URL without persisting.

Fetches the URL with EthicalScraper, parses the HTML using the site's
configuration, normalizes the result via mapper_service, and returns a
structured report — no DB writes.
"""
import asyncio

from app.core.logging import get_logger
from app.models.site_config_model import SiteConfig
from app.schemas.site_config_schema import TestScrapeNormalized, TestScrapeResponse
from app.services.ethics_service import EthicalScraper
from app.services.mapper_service import normalize_partner_payload
from app.services.parser_service import parse_listing_page

logger = get_logger(__name__)

_CRITICAL_FIELDS = ("title", "price", "property_type", "district")


async def run_test_scrape(site: SiteConfig, url: str) -> TestScrapeResponse:
    """Fetch *url* and run the full parse+normalize pipeline.

    Uses the site's selectors, extraction_mode, and site_key (for mapper
    dispatch). Never writes to the database.
    """
    scraper = EthicalScraper(user_agent="MVPScraper/1.0 (+test-scrape)")
    try:
        response = await asyncio.to_thread(scraper.get, url)
    except Exception as exc:
        logger.error("test-scrape fetch failed for %s: %s", url, exc)
        return TestScrapeResponse(url=url, success=False, error=str(exc))
    finally:
        scraper.close()

    if not response:
        return TestScrapeResponse(
            url=url,
            success=False,
            error="Failed to fetch page — blocked by robots.txt, rate limit, or HTTP error.",
        )

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        raw = parse_listing_page(
            response.text,
            url,
            site.selectors,
            site.extraction_mode,
        )
    except Exception as exc:
        logger.error("test-scrape parse failed for %s: %s", url, exc)
        return TestScrapeResponse(url=url, success=False, error=f"Parse error: {exc}")

    # Missing critical fields check (mirrors scraper_service logic)
    missing_critical = [
        f for f in _CRITICAL_FIELDS
        if not str(raw.get(f, "") or "").strip()
    ]

    # ── Normalize ─────────────────────────────────────────────────────────
    try:
        schema = normalize_partner_payload(raw, site.key)
    except Exception as exc:
        logger.warning("test-scrape normalize failed for %s: %s", url, exc)
        # Return raw output even if normalization fails
        return TestScrapeResponse(
            url=url,
            success=True,
            raw=_safe_raw(raw),
            missing_critical=missing_critical,
            error=f"Normalization error: {exc}",
        )

    normalized = TestScrapeNormalized(
        title=schema.title,
        listing_type=schema.listing_type,
        property_type=schema.property_type,
        typology=schema.typology,
        bedrooms=schema.bedrooms,
        bathrooms=schema.bathrooms,
        price_amount=schema.price.amount,
        price_currency=schema.price.currency,
        price_per_m2=schema.price_per_m2.amount if schema.price_per_m2 else None,
        area_useful_m2=schema.area_useful_m2,
        area_gross_m2=schema.area_gross_m2,
        area_land_m2=schema.area_land_m2,
        district=schema.address.region,
        county=schema.address.city,
        parish=schema.address.area,
        energy_certificate=schema.energy_certificate,
        construction_year=schema.construction_year,
        has_garage=schema.features.has_garage,
        has_pool=schema.features.has_pool,
        has_elevator=schema.features.has_elevator,
        image_count=len(schema.media),
    )

    return TestScrapeResponse(
        url=url,
        success=True,
        raw=_safe_raw(raw),
        normalized=normalized,
        missing_critical=missing_critical,
    )


def _safe_raw(raw: dict) -> dict:
    """Serialize raw dict to be JSON-safe (drop unpicklable objects, truncate long strings)."""
    result: dict = {}
    for key, value in raw.items():
        if isinstance(value, list):
            result[key] = [str(v)[:300] for v in value[:10]]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = str(value)[:300] if isinstance(value, str) else value
        else:
            result[key] = str(value)[:300]
    return result
