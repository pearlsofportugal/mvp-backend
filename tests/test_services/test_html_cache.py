"""Tests for the in-memory HTML cache."""

import pytest

from app.crawler.html_cache import HtmlCache


@pytest.mark.asyncio
async def test_html_cache_reuses_cached_value_before_ttl_expires(monkeypatch) -> None:
    cache = HtmlCache(ttl_seconds=300)
    call_count = {"fetches": 0}
    current_time = {"value": 100.0}

    async def fake_fetcher(url: str) -> str:
        call_count["fetches"] += 1
        return "<html>cached</html>"

    monkeypatch.setattr("app.crawler.html_cache.time.monotonic", lambda: current_time["value"])

    first = await cache.get_or_set("https://example.pt", fake_fetcher)
    second = await cache.get_or_set("https://example.pt", fake_fetcher)

    assert first == second == "<html>cached</html>"
    assert call_count["fetches"] == 1


@pytest.mark.asyncio
async def test_html_cache_fetches_again_after_ttl_expires(monkeypatch) -> None:
    cache = HtmlCache(ttl_seconds=10)
    call_count = {"fetches": 0}
    current_time = {"value": 50.0}

    async def fake_fetcher(url: str) -> str:
        call_count["fetches"] += 1
        return f"<html>{call_count['fetches']}</html>"

    monkeypatch.setattr("app.crawler.html_cache.time.monotonic", lambda: current_time["value"])

    first = await cache.get_or_set("https://example.pt", fake_fetcher)
    current_time["value"] = 61.0
    second = await cache.get_or_set("https://example.pt", fake_fetcher)

    assert first == "<html>1</html>"
    assert second == "<html>2</html>"
    assert call_count["fetches"] == 2