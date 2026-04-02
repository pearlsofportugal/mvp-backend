"""Service — validate CSS selectors against a live page before saving to a SiteConfig."""

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.crawler.html_cache import get_cached_html
from app.crawler.selector_suggester import fetch_html
from app.schemas.site_config_schema import (
    SelectorValidationReport,
    SelectorValidationResult,
)

logger = get_logger(__name__)

_SAMPLE_MAX_LEN = 200


async def validate_selectors(
    selectors: dict[str, str],
    url: str,
) -> SelectorValidationReport:
    """Fetch *url* once (via cache) and test each selector in *selectors*.

    Returns a :class:`SelectorValidationReport` with per-field results,
    human-readable warnings (valid CSS but 0 matches), and errors (bad CSS or
    failed page fetch).

    The report's ``success`` field is ``False`` when the page cannot be fetched
    or when at least one selector has invalid CSS syntax.
    """
    try:
        html = await get_cached_html(url, fetch_html)
    except Exception as exc:
        logger.error(
            "Selector validation: failed to fetch %s",
            url,
            extra={"url": url},
            exc_info=exc,
        )
        return SelectorValidationReport(
            url=url,
            success=False,
            results=[],
            warnings=[],
            errors=[f"Failed to fetch page: {exc}"],
        )

    soup = BeautifulSoup(html, "lxml")
    results: list[SelectorValidationResult] = []
    warnings: list[str] = []
    errors: list[str] = []

    for field, selector in selectors.items():
        try:
            elements = soup.select(selector)
        except Exception as exc:
            errors.append(f"'{field}' ({selector!r}): invalid CSS — {exc}")
            results.append(
                SelectorValidationResult(
                    field=field,
                    selector=selector,
                    valid_css=False,
                    matches=0,
                    sample=None,
                )
            )
            continue

        matches = len(elements)
        sample: str | None = None

        if elements:
            first = elements[0]
            if first.name == "img":
                sample = str(first.get("src") or first.get("data-src") or "")[:_SAMPLE_MAX_LEN] or None
            else:
                raw = first.get_text(strip=True)
                sample = raw[:_SAMPLE_MAX_LEN] or None

        results.append(
            SelectorValidationResult(
                field=field,
                selector=selector,
                valid_css=True,
                matches=matches,
                sample=sample,
            )
        )

        if matches == 0:
            warnings.append(
                f"'{field}' ({selector!r}): valid CSS but no elements matched on this page"
            )

    return SelectorValidationReport(
        url=url,
        success=len(errors) == 0,
        results=results,
        warnings=warnings,
        errors=errors,
    )
