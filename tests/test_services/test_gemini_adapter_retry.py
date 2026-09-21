"""Tests for GeminiAdapter retry/backoff behavior on transient failures."""
from unittest.mock import MagicMock

import pytest

from app.adapters import gemini_adapter as gemini_adapter_module
from app.adapters.gemini_adapter import GeminiAdapter
from app.core.exceptions import EnrichmentError


def _make_response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    return response


class TestGeminiAdapterRetry:
    def test_retries_transient_failure_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(gemini_adapter_module.time, "sleep", lambda _seconds: None)
        client = MagicMock()
        client.models.generate_content.side_effect = [
            RuntimeError("transient 503"),
            _make_response('{"pt": {"title": "ok"}}'),
        ]
        monkeypatch.setattr(gemini_adapter_module, "_get_client", lambda: client)

        result = GeminiAdapter().generate(system_instruction="sys", prompt="prompt")

        assert result == {"pt": {"title": "ok"}}
        assert client.models.generate_content.call_count == 2

    def test_raises_enrichment_error_after_exhausting_retries(self, monkeypatch):
        monkeypatch.setattr(gemini_adapter_module.time, "sleep", lambda _seconds: None)
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("persistent failure")
        monkeypatch.setattr(gemini_adapter_module, "_get_client", lambda: client)

        with pytest.raises(EnrichmentError):
            GeminiAdapter().generate(system_instruction="sys", prompt="prompt")

        assert client.models.generate_content.call_count == gemini_adapter_module._MAX_RETRIES

    def test_malformed_json_is_retried_and_recovers(self, monkeypatch):
        # The model is non-deterministic — a bad response on attempt 1 doesn't
        # mean attempt 2 will fail the same way.
        monkeypatch.setattr(gemini_adapter_module.time, "sleep", lambda _seconds: None)
        client = MagicMock()
        client.models.generate_content.side_effect = [
            _make_response("not json at all"),
            _make_response('{"pt": {"title": "recovered"}}'),
        ]
        monkeypatch.setattr(gemini_adapter_module, "_get_client", lambda: client)

        result = GeminiAdapter().generate(system_instruction="sys", prompt="prompt")

        assert result == {"pt": {"title": "recovered"}}
