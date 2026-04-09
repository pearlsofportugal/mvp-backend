"""HTTP adapter — isolates the `requests` library from EthicalScraper.

EthicalScraper uses this adapter for all outbound HTTP calls.
Swap the implementation here to use httpx or playwright without touching scraping logic.
"""
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.logging import get_logger

logger = get_logger(__name__)


class HttpAdapter:
    """Synchronous HTTP client with retry and rate-limit support.

    Wraps `requests.Session` so that no other module imports `requests` directly.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: int = 120,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._timeout = timeout
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
        self._session.headers["User-Agent"] = user_agent

        if extra_headers:
            safe = {k: v for k, v in extra_headers.items() if k.lower() != "user-agent"}
            self._session.headers.update(safe)

    def get(self, url: str) -> requests.Response | None:
        """Perform a GET request.

        Returns the Response on HTTP 200, None on 4xx or exhausted retries.
        """
        try:
            response = self._session.get(url, timeout=self._timeout)
            if response.status_code == 200:
                return response
            if 400 <= response.status_code < 500 and response.status_code != 429:
                logger.warning("HTTP %d for %s — not retrying", response.status_code, url)
                return None
            logger.warning("HTTP %d for %s", response.status_code, url)
            return None
        except requests.exceptions.Timeout:
            logger.error("Timeout (%ds) fetching %s", self._timeout, url)
            return None
        except requests.exceptions.RequestException as exc:
            logger.error("Request error for %s: %s", url, str(exc))
            return None

    def sleep_random(self, min_delay: float, max_delay: float) -> None:
        """Sleep a random duration in [min_delay, max_delay] seconds."""
        delay = random.uniform(min_delay, max_delay)
        logger.debug("Rate limiting: sleeping %.2f seconds", delay)
        time.sleep(delay)

    def close(self) -> None:
        """Release the underlying session."""
        self._session.close()
