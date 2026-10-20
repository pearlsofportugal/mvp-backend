"""PlaywrightScraper must read robots.txt like EthicalScraper (browser UA, real status codes)."""
import asyncio
from unittest.mock import MagicMock, patch

import requests

from app.services.playwright_scraper import PlaywrightScraper

_URL = "https://www.example.pt/pt/imoveis/casa_1/"


def _resp(status: int, text: str = "") -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code, r.text = status, text
    return r


def _allowed(response) -> bool:
    scraper = PlaywrightScraper()
    scraper._ROBOTS_RETRY_DELAY_SECONDS = 0
    with patch("app.services.playwright_scraper.requests.get", side_effect=response if isinstance(response, list) else None, return_value=None if isinstance(response, list) else response) as get:
        result = asyncio.run(scraper.allowed(_URL))
    return result, get


def test_forbidden_robots_txt_is_treated_as_allow_all_not_disallow_all():
    ok, _ = _allowed(_resp(403))
    assert ok is True


def test_missing_robots_txt_allows_everything():
    ok, _ = _allowed(_resp(404))
    assert ok is True


def test_disallow_rules_are_still_respected():
    ok, _ = _allowed(_resp(200, "User-agent: *\nDisallow: /pt/imoveis/\n"))
    assert ok is False


def test_robots_request_uses_a_browser_user_agent():
    _, get = _allowed(_resp(200, "User-agent: *\nAllow: /\n"))
    assert "Mozilla" in get.call_args.kwargs["headers"]["User-Agent"]


def test_unreachable_robots_txt_still_fails_closed():
    ok, _ = _allowed([requests.ConnectionError("down")] * 3)
    assert ok is False


def test_transient_5xx_is_retried():
    ok, get = _allowed([_resp(503), _resp(200, "User-agent: *\nAllow: /\n")])
    assert ok is True and get.call_count == 2
