"""Simple in-memory HTML cache with TTL semantics and LRU eviction."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(slots=True)
class CacheEntry:
    """Cached HTML payload with an absolute expiry timestamp."""

    html: str
    expires_at: float


class HtmlCache:
    """In-memory HTML cache with a fixed TTL per URL and LRU eviction.

    Thread-safety notes:
    - The asyncio.Lock on _entries protects all dict mutations.
    - Per-URL inflight locks in _inflight prevent duplicate fetches when two
      coroutines request the same uncached URL simultaneously (TOCTOU fix).
    """

    def __init__(self, ttl_seconds: int = 300, maxsize: int = 100) -> None:
        self._ttl_seconds = ttl_seconds
        self._maxsize = maxsize
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        # Per-URL fetch locks to prevent duplicate concurrent fetches
        self._inflight: dict[str, asyncio.Lock] = {}

    async def get(self, url: str) -> str | None:
        """Return cached HTML when still valid, otherwise None."""
        async with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None

            if entry.expires_at <= time.monotonic():
                self._entries.pop(url, None)
                return None

            # LRU: move to end (most-recently used)
            self._entries.move_to_end(url)
            return entry.html

    async def set(self, url: str, html: str) -> None:
        """Store HTML in the cache using the configured TTL, evicting LRU if full."""
        async with self._lock:
            if url in self._entries:
                self._entries.move_to_end(url)
            self._entries[url] = CacheEntry(
                html=html,
                expires_at=time.monotonic() + self._ttl_seconds,
            )
            # Evict oldest entries when over capacity
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    async def get_or_set(
        self,
        url: str,
        fetcher: Callable[[str], Awaitable[str]],
    ) -> str:
        """Return cached HTML or fetch-and-cache on a miss.

        Uses per-URL locks to guarantee that concurrent requests for the same
        URL result in exactly one network fetch (TOCTOU-safe).
        """
        cached = await self.get(url)
        if cached is not None:
            return cached

        # Ensure only one coroutine fetches a given URL at a time
        async with self._lock:
            if url not in self._inflight:
                self._inflight[url] = asyncio.Lock()
        url_lock = self._inflight[url]

        try:
            async with url_lock:
                # Double-check: another coroutine may have populated the cache
                cached = await self.get(url)
                if cached is not None:
                    return cached

                html = await fetcher(url)
                await self.set(url, html)
                return html
        finally:
            # Remove the per-URL lock to prevent unbounded growth
            async with self._lock:
                self._inflight.pop(url, None)

    async def clear(self) -> None:
        """Remove all entries from the cache."""
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()


html_cache = HtmlCache(ttl_seconds=300, maxsize=100)


async def get_cached_html(
    url: str,
    fetcher: Callable[[str], Awaitable[str]],
) -> str:
    """Convenience wrapper used by crawler-facing services."""
    return await html_cache.get_or_set(url, fetcher)
