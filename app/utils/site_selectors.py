"""Selector dict a scrape actually runs with: site selectors + the site-level filters."""
import re
from typing import Any


_DOUBLED_BACKSLASH_RE = re.compile(r"\\\\")


def repair_regex(pattern: str | None) -> str | None:
    """Undo double-escaping in a URL regex stored on a site config.

    A pattern saved through two layers of JSON/shell escaping reads ``imgs\\.x``
    (two backslashes) and then matches a literal backslash instead of a dot, so
    it silently rejects every URL. A literal backslash never occurs in a URL, so
    collapsing each pair is always the intended meaning. Raises ``re.error`` when
    the repaired pattern still does not compile.
    """
    if not pattern:
        return pattern
    fixed = _DOUBLED_BACKSLASH_RE.sub(lambda _: "\\", pattern)
    re.compile(fixed)
    return fixed


def effective_selectors(
    selectors: dict[str, Any] | None,
    link_pattern: str | None = None,
    image_filter: str | None = None,
    image_exclude_filter: str | None = None,
) -> dict[str, Any]:
    """Merge the site-level filters into its selectors, as the real scraper does.

    ``link_pattern``, ``image_filter`` and ``image_exclude_filter`` live in their own
    columns on the site config but the parsers read them from the selectors dict.
    Anything that dry-runs a site (test-scrape, test-listing-page) must build its
    selectors through here, otherwise it reports results the real job never produces.
    """
    merged: dict[str, Any] = {**(selectors or {})}
    for key, pattern in (
        ("listing_link_pattern", link_pattern),
        ("image_filter", image_filter),
        ("image_exclude_filter", image_exclude_filter),
    ):
        if pattern:
            try:
                merged[key] = repair_regex(pattern)
            except re.error:
                merged[key] = pattern  # invalid either way; let the parser surface it
    return merged
