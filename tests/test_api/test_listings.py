"""Tests for Listings API endpoints."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_listings_empty(client: AsyncClient):
    """GET /api/v1/listings returns empty list initially."""
    response = await client.get("/api/v1/listings")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_create_and_get_listing(client: AsyncClient):
    """POST + GET /api/v1/listings creates and retrieves a listing."""
    from tests.conftest import make_listing_payload

    payload = make_listing_payload()
    create_resp = await client.post("/api/v1/listings", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["title"] == payload["title"]
    assert created["source_partner"] == "pearls"
    assert created["bedrooms"] == 2
    assert float(created["price_amount"]) == 250000.00

    # GET by ID
    listing_id = created["id"]
    get_resp = await client.get(f"/api/v1/listings/{listing_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == listing_id


@pytest.mark.asyncio
async def test_create_duplicate_source_url(client: AsyncClient):
    """POST /api/v1/listings rejects duplicate source_url."""
    from tests.conftest import make_listing_payload

    payload = make_listing_payload()
    await client.post("/api/v1/listings", json=payload)
    dup_resp = await client.post("/api/v1/listings", json=payload)
    assert dup_resp.status_code == 409


@pytest.mark.asyncio
async def test_update_listing(client: AsyncClient):
    """PATCH /api/v1/listings/{id} updates fields."""
    from tests.conftest import make_listing_payload

    payload = make_listing_payload()
    create_resp = await client.post("/api/v1/listings", json=payload)
    listing_id = create_resp.json()["data"]["id"]

    update_resp = await client.patch(
        f"/api/v1/listings/{listing_id}",
        json={"price_amount": 275000.00, "title": "Updated Title"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["title"] == "Updated Title"
    assert float(update_resp.json()["data"]["price_amount"]) == 275000.00


@pytest.mark.asyncio
async def test_delete_listing(client: AsyncClient):
    """DELETE /api/v1/listings/{id} removes a listing."""
    from tests.conftest import make_listing_payload

    payload = make_listing_payload()
    create_resp = await client.post("/api/v1/listings", json=payload)
    listing_id = create_resp.json()["data"]["id"]

    del_resp = await client.delete(f"/api/v1/listings/{listing_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    get_resp = await client.get(f"/api/v1/listings/{listing_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_listing_stats(client: AsyncClient):
    """GET /api/v1/listings/stats returns aggregated stats."""
    from tests.conftest import make_listing_payload

    await client.post("/api/v1/listings", json=make_listing_payload(
        source_url="https://example.com/1", district="Lisboa"
    ))
    await client.post("/api/v1/listings", json=make_listing_payload(
        source_url="https://example.com/2", district="Porto", price_amount=300000.00
    ))

    resp = await client.get("/api/v1/listings/stats")
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert stats["total_listings"] == 2
    assert stats["avg_price"] is not None


@pytest.mark.asyncio
async def test_listing_filters(client: AsyncClient):
    """GET /api/v1/listings with filters works correctly."""
    from tests.conftest import make_listing_payload

    await client.post("/api/v1/listings", json=make_listing_payload(
        source_url="https://example.com/1", district="Lisboa", price_amount=200000.00
    ))
    await client.post("/api/v1/listings", json=make_listing_payload(
        source_url="https://example.com/2", district="Porto", price_amount=400000.00
    ))

    # Filter by district
    resp = await client.get("/api/v1/listings", params={"district": "Lisboa"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1

    # Filter by price range
    resp = await client.get("/api/v1/listings", params={"price_min": 300000})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1
