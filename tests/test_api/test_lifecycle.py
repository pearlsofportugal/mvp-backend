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


async def test_openapi_documents_status_and_updated_after(client: AsyncClient):
    resp = await client.get("/openapi.json")
    if resp.status_code != 200:  # docs are disabled in production-like settings
        return
    params = {p["name"]: p for p in resp.json()["paths"]["/api/v1/listings"]["get"]["parameters"]}
    assert params["status"]["schema"]["default"] == "active"
    assert "updated_after" in params and params["updated_after"]["description"]


async def test_list_and_detail_expose_coordinates(client: AsyncClient, db_session):
    listing = await _make(
        db_session, "https://x.pt/geo", latitude=41.1579, longitude=-8.6291, location_precision="exact"
    )
    item = (await client.get("/api/v1/listings")).json()["data"]["items"][0]
    assert (item["latitude"], item["longitude"], item["location_precision"]) == (41.1579, -8.6291, "exact")
    detail = (await client.get(f"/api/v1/listings/{listing.id}")).json()["data"]
    assert detail["location_precision"] == "exact"


# ── include=detail ──────────────────────────────────────────────────────────

async def test_include_detail_returns_full_records_in_one_call(client: AsyncClient, db_session):
    from app.models.media_model import MediaAsset

    for i in range(3):
        listing = await _make(db_session, f"https://x.pt/d{i}", description_clean="Texto limpo.")
        for pos in (1, 0):
            db_session.add(MediaAsset(listing_id=listing.id, url=f"https://x.pt/d{i}/{pos}.jpg", type="photo", position=pos))
    await db_session.commit()

    plain = (await client.get("/api/v1/listings")).json()
    assert "media_assets" not in plain["data"]["items"][0]          # default format unchanged

    resp = await client.get("/api/v1/listings", params={"include": "detail", "page_size": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 3
    item = body["data"]["items"][0]
    assert [m["position"] for m in item["media_assets"]] == [0, 1]
    assert item["description_clean"] == "Texto limpo."
    assert {"price_history", "status", "latitude", "is_enriched"} <= set(item)


async def test_include_detail_page_size_is_capped_and_validated(client: AsyncClient):
    too_big = await client.get("/api/v1/listings", params={"include": "detail", "page_size": 51})
    assert too_big.status_code == 422
    assert (await client.get("/api/v1/listings", params={"include": "everything"})).status_code == 422
    assert (await client.get("/api/v1/listings", params={"page_size": 100})).status_code == 200  # unchanged without include


async def test_include_detail_is_documented(client: AsyncClient):
    resp = await client.get("/openapi.json")
    if resp.status_code != 200:
        return
    params = {p["name"]: p for p in resp.json()["paths"]["/api/v1/listings"]["get"]["parameters"]}
    assert "detail" in params["include"]["description"]
