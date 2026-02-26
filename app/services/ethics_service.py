"""EthicalScraper service — preserves all ethical scraping rules.

Rules preserved:
1. Fail-closed robots.txt: if robots.txt fails to load, BLOCK all requests
2. Mandatory rate limiting: random delay between min/max BEFORE each request
3. Identifiable User-Agent: must include bot name + contact
4. Retries with exponential backoff: 429/5xx retriable, 4xx returns None
5. Per-domain robots.txt cache with 1-hour TTL
6. URL deduplication within a job
"""
import random
import re
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT_PATTERN = re.compile(r".+/.+\s*\(\+.+\)")


class EthicalScraper:
    """HTTP client that respects robots.txt, rate limits, and ethical scraping rules."""

    ROBOTS_CACHE_TTL = 3600.0 

    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        user_agent: str = "RealEstateResearchBot/1.0 (+contact: you@example.com)",
        timeout: int = 120,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self._robots_cache: dict[str, tuple[RobotFileParser, bool]] = {}
        self._cache_timestamps: dict[str, float] = {}

        self._visited_urls: set[str] = set()

        self._session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update({"User-Agent": self.user_agent})

        if not USER_AGENT_PATTERN.match(self.user_agent):
            logger.warning(
                "User-Agent '%s' does not follow identifiable bot format. "
                "Recommended: 'BotName/Version (+contact: email@example.com)'",
                self.user_agent,
            )

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _load_robots(self, domain: str) -> tuple[RobotFileParser, bool]:
        """Load and cache robots.txt for a domain."""
        now = time.time()

        if domain in self._robots_cache:
            cached_time = self._cache_timestamps.get(domain, 0)
            if now - cached_time < self.ROBOTS_CACHE_TTL:
                return self._robots_cache[domain]

        robots_url = f"{domain}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)

        loaded = False
        try:
            parser.read()
            loaded = True
            logger.info("Loaded robots.txt from %s", robots_url)
        except Exception as e:
            logger.warning(
                "Failed to load robots.txt from %s: %s — BLOCKING all requests (fail-closed)",
                robots_url,
                str(e),
            )
            loaded = False

        self._robots_cache[domain] = (parser, loaded)
        self._cache_timestamps[domain] = now
        return parser, loaded

    def allowed(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt. FAIL-CLOSED: blocks if robots.txt unavailable."""
        domain = self._get_domain(url)
        parser, loaded = self._load_robots(domain)

        if not loaded:
            logger.warning("Blocking %s — robots.txt not loaded (fail-closed)", url)
            return False

        allowed = parser.can_fetch(self.user_agent, url)
        if not allowed:
            logger.info("Blocked by robots.txt: %s", url)
        return allowed

    def _sleep(self) -> None:
        """Rate limiting: random delay BEFORE each request."""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug("Rate limiting: sleeping %.2f seconds", delay)
        time.sleep(delay)

    def is_visited(self, url: str) -> bool:
        """Check if URL has already been visited in this job."""
        return url in self._visited_urls

    def mark_visited(self, url: str) -> None:
        """Mark URL as visited."""
        self._visited_urls.add(url)

    def reset_visited(self) -> None:
        """Reset visited URLs (for a new job)."""
        self._visited_urls.clear()

    def get(self, url: str) -> Optional[requests.Response]:
        """
        Fetch a URL ethically.

        Returns the Response on success, or None if:
        - URL is blocked by robots.txt
        - URL was already visited
        - HTTP 4xx (non-retriable)
        - All retries exhausted
        """
        if self.is_visited(url):
            logger.debug("Skipping already visited URL: %s", url)
            return None

        if not self.allowed(url):
            return None

        self._sleep()

        self.mark_visited(url)

        try:
            response = self._session.get(url, timeout=self.timeout)

            if response.status_code == 200:
                return response
            elif 400 <= response.status_code < 500 and response.status_code != 429:
                logger.warning("HTTP %d for %s — not retrying", response.status_code, url)
                return None
            else:
                logger.warning("HTTP %d for %s", response.status_code, url)
                return None

        except requests.exceptions.Timeout:
            logger.error("Timeout (%ds) fetching %s", self.timeout, url)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Request error for %s: %s", url, str(e))
            return None

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()
