"""Tests for export API endpoints."""
import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_export_json_returns_filtered_rows(client: AsyncClient):
    """GET /api/v1/export/json returns a downloadable JSON payload."""
    from tests.conftest import make_listing_payload

    await client.post("/api/v1/listings", json=make_listing_payload(source_url="https://example.com/export-1"))

    response = await client.get("/api/v1/export/json")

    assert response.status_code == 200
    rows = json.loads(response.text)
    assert len(rows) == 1
    assert rows[0]["source_url"] == "https://example.com/export-1"


@pytest.mark.asyncio
async def test_export_json_rejects_over_limit(client: AsyncClient, monkeypatch):
    """GET /api/v1/export/json refuses exports above the configured cap."""
    from tests.conftest import make_listing_payload

    monkeypatch.setattr("app.api.v1.export.settings.export_max_rows", 1)

    await client.post("/api/v1/listings", json=make_listing_payload(source_url="https://example.com/export-1"))
    await client.post(
        "/api/v1/listings",
        json=make_listing_payload(source_url="https://example.com/export-2", title="Second export listing"),
    )

    response = await client.get("/api/v1/export/json")

    assert response.status_code == 400
    assert "Export exceeds maximum row limit" in response.json()["message"]