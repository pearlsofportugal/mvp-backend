"""Tests for Site Configs API endpoints."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_sites_empty(client: AsyncClient):
    """GET /api/v1/sites returns empty list initially."""
    response = await client.get("/api/v1/sites")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == []


@pytest.mark.asyncio
async def test_create_and_get_site(client: AsyncClient):
    """POST + GET /api/v1/sites creates and retrieves a site config."""
    from tests.conftest import make_site_config_payload

    payload = make_site_config_payload()
    create_resp = await client.post("/api/v1/sites", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["key"] == "test_site"
    assert created["name"] == "Test Site"

    # GET by key
    get_resp = await client.get("/api/v1/sites/test_site")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["key"] == "test_site"


@pytest.mark.asyncio
async def test_update_site(client: AsyncClient):
    """PATCH /api/v1/sites/{key} updates a site config."""
    from tests.conftest import make_site_config_payload

    await client.post("/api/v1/sites", json=make_site_config_payload())

    update_resp = await client.patch(
        "/api/v1/sites/test_site",
        json={"name": "Updated Site", "is_active": False},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["name"] == "Updated Site"
    assert update_resp.json()["data"]["is_active"] is False


@pytest.mark.asyncio
async def test_delete_site_deactivates(client: AsyncClient):
    """DELETE /api/v1/sites/{key} soft-deletes (deactivates) the site."""
    from tests.conftest import make_site_config_payload

    await client.post("/api/v1/sites", json=make_site_config_payload())

    del_resp = await client.delete("/api/v1/sites/test_site")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    # Still retrievable but inactive
    get_resp = await client.get("/api/v1/sites/test_site")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["is_active"] is False


@pytest.mark.asyncio
async def test_duplicate_site_key(client: AsyncClient):
    """POST /api/v1/sites rejects duplicate key."""
    from tests.conftest import make_site_config_payload

    await client.post("/api/v1/sites", json=make_site_config_payload())
    dup_resp = await client.post("/api/v1/sites", json=make_site_config_payload())
    assert dup_resp.status_code == 409
