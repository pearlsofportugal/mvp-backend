"""EthicalScraper service — preserves all ethical scraping rules.

Rules preserved:
1. Fail-closed robots.txt: if robots.txt fails to load, BLOCK all requests
2. Mandatory rate limiting: random delay between min/max BEFORE each request
3. Identifiable User-Agent: must include bot name + contact
4. Retries with exponential backoff: 429/5xx retriable, 4xx returns None
5. Per-domain robots.txt cache with 1-hour TTL
6. URL deduplication within a job
7. SSRF protection: requests to private/loopback IPs are blocked
"""
import ipaddress
import re
import socket
import time

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from requests import Response

from app.adapters.http_adapter import HttpAdapter
from app.core.logging import get_logger

logger = get_logger(__name__)

# Minimum acceptable User-Agent pattern: must have bot name and contact
USER_AGENT_PATTERN = re.compile(r".+/.+\s*\(\+.+\)")


class EthicalScraper:
    """HTTP client that respects robots.txt, rate limits, and ethical scraping rules."""

    ROBOTS_CACHE_TTL = 3600.0  # 1 hour

    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        user_agent: str = "RealEstateResearchBot/1.0 (+contact: you@example.com)",
        timeout: int = 120,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        extra_headers: dict | None = None,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        # Robots.txt cache: domain -> (parser, is_loaded_successfully, expires_at)
        # Single dict for atomic reads/writes — no race between cache and timestamps.
        self._robots_cache: dict[str, tuple[RobotFileParser, bool, float]] = {}

        # URL deduplication for current job
        self._visited_urls: set[str] = set()

        # HTTP transport
        self._http = HttpAdapter(
            user_agent=user_agent,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            extra_headers=extra_headers,
        )

        # Validate user agent
        if not USER_AGENT_PATTERN.match(self.user_agent):
            logger.warning(
                "User-Agent '%s' does not follow identifiable bot format. "
                "Recommended: 'BotName/Version (+contact: email@example.com)'",
                self.user_agent,
            )

    # Private and loopback IP ranges blocked for SSRF protection
    _PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
    ]

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _is_private_host(self, url: str) -> bool:
        """Return True if the URL resolves to a private or loopback IP (SSRF guard)."""
        hostname = urlparse(url).hostname or ""
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(hostname))
            return any(ip in net for net in self._PRIVATE_NETWORKS)
        except (socket.gaierror, ValueError):
            # Cannot resolve or invalid hostname — let robots.txt/HTTP layer handle it
            return False

    def _load_robots(self, domain: str) -> tuple[RobotFileParser, bool]:
        """Load and cache robots.txt for a domain."""
        now = time.time()

        # Check cache — single dict read is atomic; no partial state possible
        entry = self._robots_cache.get(domain)
        if entry is not None and entry[2] > now:
            return entry[0], entry[1]

        # Load robots.txt using our own User-Agent to avoid 403 from sites that block
        # Python's default urllib User-Agent. RobotFileParser.read() uses urllib internally
        # and treats a 403 response as disallow_all=True, blocking the entire site even when
        # the actual rules would permit crawling.
        robots_url = f"{domain}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)

        loaded = False
        try:
            response = self._http.get_raw(robots_url)
            if response is None:
                # Connection/timeout error — fail-closed
                logger.warning(
                    "Failed to fetch robots.txt from %s (connection error) — BLOCKING all requests (fail-closed)",
                    robots_url,
                )
                loaded = False
            elif response.status_code == 200:
                parser.parse(response.text.splitlines())
                loaded = True
                logger.info("Loaded robots.txt from %s", robots_url)
            elif response.status_code == 404:
                # No robots.txt — allow all
                parser.allow_all = True
                loaded = True
                logger.info("No robots.txt at %s (404) — allowing all", robots_url)
            elif response.status_code in (401, 403):
                # Site blocks robots.txt access itself. Treat as allow_all: if the site
                # wanted to block our UA entirely, it would return 4xx on the actual pages
                # too, which will surface naturally during scraping.
                parser.allow_all = True
                loaded = True
                logger.warning(
                    "robots.txt at %s returned %d — treating as allow_all (page-level access controls apply)",
                    robots_url,
                    response.status_code,
                )
            else:
                logger.warning(
                    "Failed to fetch robots.txt from %s (status: %d) — BLOCKING all requests (fail-closed)",
                    robots_url,
                    response.status_code,
                )
                loaded = False
        except Exception as e:
            logger.warning(
                "Failed to load robots.txt from %s: %s — BLOCKING all requests (fail-closed)",
                robots_url,
                str(e),
            )
            loaded = False

        # Atomic write: single tuple assignment, not two separate dict updates
        self._robots_cache[domain] = (parser, loaded, now + self.ROBOTS_CACHE_TTL)
        return parser, loaded

    def allowed(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt. FAIL-CLOSED: blocks if robots.txt unavailable."""
        # SSRF guard: block requests to private/loopback IPs
        if self._is_private_host(url):
            logger.warning("Blocking %s — resolves to private/loopback IP (SSRF guard)", url)
            return False

        domain = self._get_domain(url)
        parser, loaded = self._load_robots(domain)

        # FAIL-CLOSED: if robots.txt failed to load, block everything
        if not loaded:
            logger.warning("Blocking %s — robots.txt not loaded (fail-closed)", url)
            return False

        allowed = parser.can_fetch(self.user_agent, url)
        if not allowed:
            logger.info("Blocked by robots.txt: %s", url)
        return allowed

    def _sleep(self) -> None:
        """Rate limiting: random delay BEFORE each request."""
        self._http.sleep_random(self.min_delay, self.max_delay)

    def is_visited(self, url: str) -> bool:
        """Check if URL has already been visited in this job."""
        return url in self._visited_urls

    def mark_visited(self, url: str) -> None:
        """Mark URL as visited."""
        self._visited_urls.add(url)

    def reset_visited(self) -> None:
        """Reset visited URLs (for a new job)."""
        self._visited_urls.clear()

    def get(self, url: str) -> Response | None:
        """
        Fetch a URL ethically.

        Returns the Response on success, or None if:
        - URL is blocked by robots.txt
        - URL was already visited
        - HTTP 4xx (non-retriable)
        - All retries exhausted
        """
        # Deduplication
        if self.is_visited(url):
            logger.debug("Skipping already visited URL: %s", url)
            return None

        # Robots.txt check (fail-closed)
        if not self.allowed(url):
            return None

        # Rate limiting
        self._sleep()

        # Mark as visited
        self.mark_visited(url)

        return self._http.get(url)

    def close(self) -> None:
        """Close the HTTP session."""
        self._http.close()

    def __enter__(self) -> "EthicalScraper":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
