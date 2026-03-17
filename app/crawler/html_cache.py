"""Simple in-memory HTML cache with TTL semantics."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(slots=True)
class CacheEntry:
    """Cached HTML payload with an absolute expiry timestamp."""

    html: str
    expires_at: float


class HtmlCache:
    """In-memory HTML cache with a fixed TTL per URL."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, url: str) -> str | None:
        """Return cached HTML when still valid, otherwise None."""
        async with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None

            if entry.expires_at <= time.monotonic():
                self._entries.pop(url, None)
                return None

            return entry.html

    async def set(self, url: str, html: str) -> None:
        """Store HTML in the cache using the configured TTL."""
        async with self._lock:
            self._entries[url] = CacheEntry(
                html=html,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    async def get_or_set(
        self,
        url: str,
        fetcher: Callable[[str], Awaitable[str]],
    ) -> str:
        """Return cached HTML or fetch and cache it on a miss."""
        cached_html = await self.get(url)
        if cached_html is not None:
            return cached_html

        html = await fetcher(url)
        await self.set(url, html)
        return html

    async def clear(self) -> None:
        """Remove all entries from the cache."""
        async with self._lock:
            self._entries.clear()


html_cache = HtmlCache(ttl_seconds=300)


async def get_cached_html(
    url: str,
    fetcher: Callable[[str], Awaitable[str]],
) -> str:
    """Convenience wrapper used by crawler-facing services."""
    return await html_cache.get_or_set(url, fetcher)
