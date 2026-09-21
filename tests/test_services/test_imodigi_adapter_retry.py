"""Tests for the Imodigi adapter's HTTP retry/backoff behavior."""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.adapters import imodigi_adapter as imodigi_adapter_module
from app.adapters.imodigi_adapter import _request_with_retry
from app.core.exceptions import ImodigiError


def _make_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


class TestImodigiRequestRetry:
    async def test_retries_5xx_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(imodigi_adapter_module.asyncio, "sleep", AsyncMock())
        client = MagicMock()
        client.request = AsyncMock(side_effect=[_make_response(503), _make_response(200)])

        response = await _request_with_retry(client, "GET", "https://x/y")

        assert response.status_code == 200
        assert client.request.call_count == 2

    async def test_retries_transport_error_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(imodigi_adapter_module.asyncio, "sleep", AsyncMock())
        client = MagicMock()
        client.request = AsyncMock(side_effect=[httpx.ConnectTimeout("timeout"), _make_response(200)])

        response = await _request_with_retry(client, "GET", "https://x/y")

        assert response.status_code == 200

    async def test_raises_after_exhausting_retries_on_transport_error(self, monkeypatch):
        monkeypatch.setattr(imodigi_adapter_module.asyncio, "sleep", AsyncMock())
        client = MagicMock()
        client.request = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with pytest.raises(ImodigiError):
            await _request_with_retry(client, "GET", "https://x/y")

        assert client.request.call_count == imodigi_adapter_module._MAX_RETRIES

    async def test_non_retryable_status_returns_immediately_without_retrying(self, monkeypatch):
        monkeypatch.setattr(imodigi_adapter_module.asyncio, "sleep", AsyncMock())
        client = MagicMock()
        client.request = AsyncMock(return_value=_make_response(404))

        response = await _request_with_retry(client, "GET", "https://x/y")

        assert response.status_code == 404
        assert client.request.call_count == 1
