"""Tests for Scrape Jobs API endpoints."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_jobs_empty(client: AsyncClient):
    """GET /api/v1/jobs returns empty list initially."""
    response = await client.get("/api/v1/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == []
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 20
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_create_job_requires_site(client: AsyncClient):
    """POST /api/v1/jobs fails without valid site config."""
    payload = {
        "site_key": "nonexistent",
        "start_url": "https://example.com/properties",
        "max_pages": 2,
    }
    resp = await client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_job_with_site(client: AsyncClient):
    """POST /api/v1/jobs succeeds with a valid site config."""
    from tests.conftest import make_site_config_payload

    # First create a site config
    await client.post("/api/v1/sites", json=make_site_config_payload())

    payload = {
        "site_key": "test_site",
        "start_url": "https://test.example.com/properties",
        "max_pages": 2,
    }
    resp = await client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    job = body["data"]
    assert job["site_key"] == "test_site"
    assert job["status"] == "pending"
    assert job["max_pages"] == 2


@pytest.mark.asyncio
async def test_get_job(client: AsyncClient):
    """GET /api/v1/jobs/{id} retrieves a job."""
    from tests.conftest import make_site_config_payload

    await client.post("/api/v1/sites", json=make_site_config_payload())

    create_resp = await client.post("/api/v1/jobs", json={
        "site_key": "test_site",
        "start_url": "https://test.example.com",
        "max_pages": 1,
    })
    job_id = create_resp.json()["data"]["id"]

    get_resp = await client.get(f"/api/v1/jobs/{job_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == job_id
