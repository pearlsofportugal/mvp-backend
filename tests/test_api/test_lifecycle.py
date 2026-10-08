"""Listing lifecycle: status filter, soft-delete and the old-client contract."""
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from app.models.listing_model import Listing
from tests.conftest import make_listing_payload


async def _make(db, url: str, **kw) -> Listing:
    listing = Listing(source_partner="pearls", source_url=url, title=kw.pop("title", "Apartamento T2"), **kw)
    db.add(listing)
    await db.commit()
    return listing


async def test_list_hides_removed_by_default(client: AsyncClient, db_session):
    await _make(db_session, "https://x.pt/a")
    await _make(db_session, "https://x.pt/b", status="removed", removed_at=datetime.now(timezone.utc))

    default = (await client.get("/api/v1/listings")).json()
    assert default["meta"]["total"] == 1
    assert default["data"]["items"][0]["status"] == "active"

    removed = (await client.get("/api/v1/listings?status=removed")).json()
    assert removed["meta"]["total"] == 1
    assert removed["data"]["items"][0]["status"] == "removed"

    everything = (await client.get("/api/v1/listings?status=all")).json()
    assert everything["meta"]["total"] == 2


async def test_status_param_is_validated(client: AsyncClient):
    assert (await client.get("/api/v1/listings?status=bogus")).status_code == 422


async def test_detail_still_serves_removed_listing(client: AsyncClient, db_session):
    listing = await _make(db_session, "https://x.pt/gone", status="removed", removed_at=datetime.now(timezone.utc))
    body = (await client.get(f"/api/v1/listings/{listing.id}")).json()["data"]
    assert body["status"] == "removed"
    assert body["removed_at"] is not None


async def test_updated_after_combines_with_status(client: AsyncClient, db_session):
    old = await _make(db_session, "https://x.pt/old", status="removed")
    old.updated_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()
    await _make(db_session, "https://x.pt/new", status="removed")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = await client.get("/api/v1/listings", params={"status": "removed", "updated_after": cutoff})
    assert resp.status_code == 200
    urls = [i["source_url"] for i in resp.json()["data"]["items"]]
    assert urls == ["https://x.pt/new"]


async def test_stats_and_selector_ignore_removed(client: AsyncClient, db_session):
    await _make(db_session, "https://x.pt/a")
    await _make(db_session, "https://x.pt/b", status="removed")
    stats = (await client.get("/api/v1/listings/stats")).json()["data"]
    assert stats["total_listings"] == 1
    selector = (await client.get("/api/v1/listings/selector")).json()
    assert selector["meta"]["total"] == 1


async def test_old_client_fields_unchanged(client: AsyncClient):
    """Every field a pre-lifecycle consumer reads is still present."""
    created = (await client.post("/api/v1/listings", json=make_listing_payload())).json()["data"]
    detail = (await client.get(f"/api/v1/listings/{created['id']}")).json()["data"]
    listed = (await client.get("/api/v1/listings")).json()["data"]["items"][0]
    for key in ("id", "title", "source_partner", "source_url", "price_amount", "created_at", "updated_at",
                "media_assets", "price_history", "latitude", "longitude", "is_enriched"):
        assert key in detail, key
    for key in ("id", "title", "source_partner", "business_type", "property_type", "typology", "price_amount",
                "district", "county", "bedrooms", "created_at", "updated_at"):
        assert key in listed, key
    assert detail["status"] == "active"
