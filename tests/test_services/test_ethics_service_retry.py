"""Tests for EthicalScraper's robots.txt fetch retry/backoff behavior."""
from unittest.mock import MagicMock

from app.services.ethics_service import EthicalScraper


def _make_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


class TestFetchRobotsResponse:
    def test_retries_transient_5xx_then_succeeds(self, monkeypatch):
        scraper = EthicalScraper()
        monkeypatch.setattr(scraper, "_http", MagicMock())
        scraper._http.get_raw.side_effect = [_make_response(503), _make_response(200)]
        monkeypatch.setattr("app.services.ethics_service.time.sleep", lambda _s: None)

        response = scraper._fetch_robots_response("https://example.com/robots.txt")

        assert response.status_code == 200
        assert scraper._http.get_raw.call_count == 2

    def test_retries_connection_error_then_succeeds(self, monkeypatch):
        scraper = EthicalScraper()
        monkeypatch.setattr(scraper, "_http", MagicMock())
        scraper._http.get_raw.side_effect = [None, _make_response(200)]
        monkeypatch.setattr("app.services.ethics_service.time.sleep", lambda _s: None)

        response = scraper._fetch_robots_response("https://example.com/robots.txt")

        assert response.status_code == 200
        assert scraper._http.get_raw.call_count == 2

    def test_gives_up_after_exhausting_attempts(self, monkeypatch):
        scraper = EthicalScraper()
        monkeypatch.setattr(scraper, "_http", MagicMock())
        scraper._http.get_raw.return_value = None
        monkeypatch.setattr("app.services.ethics_service.time.sleep", lambda _s: None)

        response = scraper._fetch_robots_response("https://example.com/robots.txt")

        assert response is None
        assert scraper._http.get_raw.call_count == scraper._ROBOTS_FETCH_ATTEMPTS

    def test_permanent_4xx_does_not_retry(self, monkeypatch):
        scraper = EthicalScraper()
        monkeypatch.setattr(scraper, "_http", MagicMock())
        scraper._http.get_raw.return_value = _make_response(404)
        monkeypatch.setattr("app.services.ethics_service.time.sleep", lambda _s: None)

        response = scraper._fetch_robots_response("https://example.com/robots.txt")

        assert response.status_code == 404
        assert scraper._http.get_raw.call_count == 1
