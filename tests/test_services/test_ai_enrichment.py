"""Tests for AI enrichment service helpers."""

import pytest

from app.core.exceptions import EnrichmentError
from app.services import ai_enrichment_service


class TestAiRateLimit:
    def test_check_ai_rate_limit_blocks_requests_above_window_limit(self, monkeypatch):
        monkeypatch.setattr(ai_enrichment_service.settings, "ai_rate_limit_requests", 2)
        monkeypatch.setattr(ai_enrichment_service.settings, "ai_rate_limit_window", 60)
        ai_enrichment_service._AI_REQUEST_TIMESTAMPS.clear()

        ai_enrichment_service._check_ai_rate_limit(now=0.0)
        ai_enrichment_service._check_ai_rate_limit(now=1.0)

        with pytest.raises(EnrichmentError):
            ai_enrichment_service._check_ai_rate_limit(now=2.0)

        ai_enrichment_service._AI_REQUEST_TIMESTAMPS.clear()

    def test_check_ai_rate_limit_releases_requests_after_window_expires(self, monkeypatch):
        monkeypatch.setattr(ai_enrichment_service.settings, "ai_rate_limit_requests", 1)
        monkeypatch.setattr(ai_enrichment_service.settings, "ai_rate_limit_window", 10)
        ai_enrichment_service._AI_REQUEST_TIMESTAMPS.clear()

        ai_enrichment_service._check_ai_rate_limit(now=0.0)
        ai_enrichment_service._check_ai_rate_limit(now=10.1)

        assert len(ai_enrichment_service._AI_REQUEST_TIMESTAMPS) == 1
        ai_enrichment_service._AI_REQUEST_TIMESTAMPS.clear()