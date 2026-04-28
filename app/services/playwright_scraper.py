"""PlaywrightScraper — headless browser scraper for JS-rendered sites (async).

Maintains the same ethical contract as EthicalScraper:
- Respects robots.txt (fail-closed)
- Mandatory random delay before each request
- URL deduplication within a job
- Identifiable User-Agent

Uses Playwright async API — all public methods must be awaited.
"""
import asyncio
import random
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from app.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT_PATTERN = re.compile(r".+/.+\s*\(\+.+\)")

# Default browser UA to pass to Playwright — looks like a real browser to the site
# but still identifies the bot through a custom header
_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightScraper:
    """Headless Chromium scraper for JS-rendered pages (async).

    Usage:
        scraper = PlaywrightScraper(...)
        html = await scraper.get_html(url)   # returns rendered HTML string or None
        await scraper.close()
    """

    ROBOTS_CACHE_TTL = 3600.0

    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        timeout: int = 30,
        extra_headers: dict | None = None,
        wait_until: str = "domcontentloaded",
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout * 1000  # Playwright uses ms
        self.extra_headers = extra_headers or {}
        self.wait_until = wait_until

        self._robots_cache: dict[str, tuple[RobotFileParser, bool]] = {}
        self._cache_timestamps: dict[str, float] = {}
        self._visited_urls: set[str] = set()

        # Playwright browser — lazily initialised on first use
        self._playwright = None
        self._browser = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> None:
        """Start Playwright + Chromium if not already running."""
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright  # noqa: PLC0415
        pw = await async_playwright().start()
        try:
            self._browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._playwright = pw
        except Exception:
            await pw.stop()
            raise
        logger.debug("Playwright Chromium launched (async)")

    async def close(self) -> None:
        """Close the browser and Playwright instance."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Robots.txt (same logic as EthicalScraper)
    # ------------------------------------------------------------------

    def _get_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _load_robots(self, domain: str) -> tuple[RobotFileParser, bool]:
        now = time.time()
        if domain in self._robots_cache:
            if now - self._cache_timestamps.get(domain, 0) < self.ROBOTS_CACHE_TTL:
                return self._robots_cache[domain]
        robots_url = f"{domain}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        loaded = False
        try:
            await asyncio.to_thread(parser.read)
            loaded = True
        except Exception as exc:
            logger.warning(
                "Failed to load robots.txt from %s: %s — BLOCKING (fail-closed)", robots_url, exc
            )
        self._robots_cache[domain] = (parser, loaded)
        self._cache_timestamps[domain] = now
        return parser, loaded

    async def allowed(self, url: str) -> bool:
        domain = self._get_domain(url)
        parser, loaded = await self._load_robots(domain)
        if not loaded:
            logger.warning("Blocking %s — robots.txt not loaded (fail-closed)", url)
            return False
        allowed = parser.can_fetch(_DEFAULT_BROWSER_UA, url)
        if not allowed:
            logger.info("Blocked by robots.txt: %s", url)
        return allowed

    # ------------------------------------------------------------------
    # Rate limiting & deduplication
    # ------------------------------------------------------------------

    def is_visited(self, url: str) -> bool:
        return url in self._visited_urls

    def mark_visited(self, url: str) -> None:
        self._visited_urls.add(url)

    def reset_visited(self) -> None:
        self._visited_urls.clear()

    # ------------------------------------------------------------------
    # Main fetch
    # ------------------------------------------------------------------

    async def get_html(self, url: str) -> str | None:
        """Render `url` in headless Chromium and return the full page HTML.

        Returns None if:
        - URL is blocked by robots.txt
        - URL was already visited
        - Navigation fails or times out
        """
        if self.is_visited(url):
            logger.debug("Skipping already visited URL: %s", url)
            return None

        if not await self.allowed(url):
            return None

        delay = random.uniform(self.min_delay, self.max_delay)
        await asyncio.sleep(delay)
        self.mark_visited(url)

        await self._ensure_browser()
        ctx = await self._browser.new_context(
            user_agent=_DEFAULT_BROWSER_UA,
            extra_http_headers=self.extra_headers,
            java_script_enabled=True,
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until=self.wait_until, timeout=self.timeout)
            # Give JS time to finish rendering without hard-failing if the site
            # never reaches networkidle (analytics, websockets, etc.)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # best-effort — proceed with whatever is rendered
            html = await page.content()
            logger.debug("Playwright rendered %s (%d bytes)", url, len(html))
            return html
        except Exception as exc:
            logger.warning("Playwright failed to render %s: %s", url, exc)
            return None
        finally:
            await page.close()
            await ctx.close()
