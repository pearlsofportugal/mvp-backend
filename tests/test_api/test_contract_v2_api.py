"""/api/v2: the contract feed, its pagination and the auxiliary endpoints."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import jsonschema
from httpx import AsyncClient

from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from tests.conftest import make_site_config_payload

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "app" / "contracts" / "listing-2.0.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
BASE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DESCRIPTION = "Apartamento T2 remodelado, com 90 m2 em zona calma, perto de comércio, escolas e transportes públicos."


def _listing(i: int, *, partner="realkey", status="active", minutes=None, **kw) -> Listing:
    at = BASE + timedelta(minutes=i if minutes is None else minutes)
    listing = Listing(
        id=uuid4(), source_partner=partner, source_url=f"https://www.{partner}.pt/imovel/{i}", partner_id=f"REF-{i}",
        title=f"Apartamento T2 número {i} em Canidelo", business_type="sale", property_type="Apartamento", typology="T2",
        price_amount=200000 + i, district="Porto", county="Vila Nova de Gaia", parish="Canidelo",
        district_code="13", county_code="1317", parish_code="131704", area_useful_m2=90.0, energy_certificate="C",
        description_clean=DESCRIPTION, status=status, created_at=at, updated_at=at, first_seen_at=at, last_seen_at=at,
        content_hash="b" * 64,
    )
    for key, value in kw.items():
        setattr(listing, key, value)
    listing.media_assets = [
        MediaAsset(id=uuid4(), url=f"https://imgs.centralimo.pt/1/properties/{n:08x}-0000-0000-0000-000000000000.jpg", type="photo", position=n)
        for n in range(3)
    ]
    return listing


async def _seed(db, count: int, **kw) -> list[Listing]:
    rows = [_listing(i, **kw) for i in range(count)]
    db.add_all(rows)
    await db.commit()
    return rows


async def _walk(client: AsyncClient, **params) -> tuple[list[dict], list[dict]]:
    """Follow next_cursor to the end: (items, pages)."""
    items, pages, cursor = [], [], None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/api/v2/listings", params=query)).json()
        pages.append(body)
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items, pages


# ── envelope and schema ─────────────────────────────────────────────────────────────────────────────

async def test_feed_envelope_and_every_item_satisfies_the_schema(client: AsyncClient, db_session):
    await _seed(db_session, 3)
    response = await client.get("/api/v2/listings")
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"schema_version", "items", "next_cursor", "total", "generated_at"}
    assert body["schema_version"] == "2.0" and body["total"] == 3 and body["next_cursor"] is None
    for item in body["items"]:
        assert [e.message for e in VALIDATOR.iter_errors(item)] == []


async def test_empty_feed_is_valid(client: AsyncClient):
    body = (await client.get("/api/v2/listings")).json()
    assert body["items"] == [] and body["total"] == 0 and body["next_cursor"] is None


# ── pagination ──────────────────────────────────────────────────────────────────────────────────────

async def test_cursor_pages_cover_everything_in_order_without_gaps_or_repeats(client: AsyncClient, db_session):
    rows = await _seed(db_session, 7)
    items, pages = await _walk(client, limit=3)
    assert [len(p["items"]) for p in pages] == [3, 3, 1]
    assert [i["id"] for i in items] == [str(r.id) for r in sorted(rows, key=lambda r: r.updated_at)]
    assert len({i["id"] for i in items}) == 7
    assert all(p["total"] == 7 for p in pages)


async def test_items_with_the_same_updated_at_are_not_lost_across_a_page_boundary(client: AsyncClient, db_session):
    rows = [_listing(i, minutes=5) for i in range(6)]  # identical updated_at
    db_session.add_all(rows)
    await db_session.commit()
    items, _ = await _walk(client, limit=2)
    assert sorted(i["id"] for i in items) == sorted(str(r.id) for r in rows)


async def test_updated_since_returns_only_the_recent_ones_and_is_inclusive(client: AsyncClient, db_session):
    rows = await _seed(db_session, 5)
    boundary = sorted(rows, key=lambda r: r.updated_at)[2].updated_at
    body = (await client.get("/api/v2/listings", params={"updated_since": boundary.isoformat()})).json()
    assert body["total"] == 3 and len(body["items"]) == 3


async def test_removed_listings_stay_in_the_feed_unless_filtered_out(client: AsyncClient, db_session):
    db_session.add_all([_listing(1), _listing(2, status="removed", removed_at=BASE)])
    await db_session.commit()
    everything = (await client.get("/api/v2/listings")).json()
    assert {i["status"] for i in everything["items"]} == {"active", "removed"}
    removed = next(i for i in everything["items"] if i["status"] == "removed")
    assert removed["removed_reason"] == "not_found" and removed["quality"]["publishable"] is False
    only_active = (await client.get("/api/v2/listings", params={"status": "active"})).json()
    assert [i["status"] for i in only_active["items"]] == ["active"]
    only_removed = (await client.get("/api/v2/listings", params={"status": "removed"})).json()
    assert [i["status"] for i in only_removed["items"]] == ["removed"]


async def test_partner_filter(client: AsyncClient, db_session):
    db_session.add_all([_listing(1, partner="realkey"), _listing(2, partner="t2mais1"), _listing(3, partner="t2mais1")])
    await db_session.commit()
    body = (await client.get("/api/v2/listings", params={"partner": "t2mais1"})).json()
    assert body["total"] == 2 and {i["source"]["partner_key"] for i in body["items"]} == {"t2mais1"}


async def test_invalid_cursor_and_limit_are_rejected(client: AsyncClient):
    assert (await client.get("/api/v2/listings", params={"cursor": "not-a-cursor"})).status_code == 400
    assert (await client.get("/api/v2/listings", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v2/listings", params={"limit": 201})).status_code == 422


# ── single listing, partner, auth ───────────────────────────────────────────────────────────────────

async def test_single_listing_and_404(client: AsyncClient, db_session):
    row = (await _seed(db_session, 1))[0]
    ok = await client.get(f"/api/v2/listings/{row.id}")
    assert ok.status_code == 200 and ok.json()["id"] == str(row.id)
    assert (await client.get(f"/api/v2/listings/{uuid4()}")).status_code == 404


async def test_partner_profile_comes_from_the_site_config(client: AsyncClient, db_session):
    await client.post("/api/v1/sites", json=make_site_config_payload(key="realkey", name="Realkey", base_url="https://www.realkey.pt/pt-PT/Imoveis"))
    patched = await client.patch("/api/v1/sites/realkey", json={"logo_url": "https://cdn.example.com/realkey.png", "legal_name": "Realkey Lda", "ami_licence": "1234"})
    assert patched.status_code == 200
    await _seed(db_session, 1)
    partner = (await client.get("/api/v2/listings")).json()["items"][0]["partner"]
    assert partner["key"] == "realkey" and partner["name"] == "Realkey" and partner["logo_url"] == "https://cdn.example.com/realkey.png"
    assert partner["legal_name"] == "Realkey Lda" and partner["ami_licence"] == "1234" and partner["website"] == "https://www.realkey.pt"


async def test_the_feed_needs_the_api_key_but_the_schema_does_not(client: AsyncClient):
    denied = await client.get("/api/v2/listings", headers={"X-API-Key": "wrong"})
    assert denied.status_code in (401, 403)
    public = await client.get("/api/v2/schemas/listing/2.0.json", headers={"X-API-Key": "wrong"})
    assert public.status_code == 200 and public.json()["$id"] == SCHEMA["$id"]


# ── auxiliary endpoints ─────────────────────────────────────────────────────────────────────────────

async def test_taxonomies(client: AsyncClient):
    body = (await client.get("/api/v2/taxonomies")).json()
    codes = {p["code"] for p in body["property_types"]}
    assert {"apartment", "house", "land", "other"} <= codes
    assert len(body["features"]) == 26 and {"A+", "exempt", "pending"} <= {e["code"] for e in body["energy_classes"]}
    assert {c["code"] for c in body["conditions"]} == {"new", "under_construction", "used", "renovated", "to_renovate", "ruin"}
    apartment = next(p for p in body["property_types"] if p["code"] == "apartment")
    assert {s["code"] for s in apartment["subtypes"]} == {"duplex", "penthouse", "studio", "loft"}


async def test_places_lists_official_districts_counties_and_parishes(client: AsyncClient):
    districts = (await client.get("/api/v2/taxonomies/places", params={"level": "district"})).json()["items"]
    assert {"code": "13", "name": "Porto", "slug": "porto"} in districts
    counties = (await client.get("/api/v2/taxonomies/places", params={"level": "county", "parent": "13"})).json()["items"]
    assert any(c["code"] == "1317" and c["name"] == "Vila Nova de Gaia" for c in counties) and all(c["code"].startswith("13") for c in counties)
    parishes = (await client.get("/api/v2/taxonomies/places", params={"level": "parish", "parent": "1317"})).json()["items"]
    assert any(p["name"] == "Canidelo" for p in parishes)
    assert (await client.get("/api/v2/taxonomies/places", params={"level": "city"})).status_code == 422


async def test_contract_health_counts_per_partner(client: AsyncClient, db_session):
    await client.post("/api/v1/sites", json=make_site_config_payload(key="realkey", name="Realkey", base_url="https://www.realkey.pt"))
    db_session.add_all([_listing(1), _listing(2), _listing(3, status="removed")])
    await db_session.commit()
    body = (await client.get("/api/v2/health")).json()
    realkey = next(p for p in body["partners"] if p["key"] == "realkey")
    assert body["schema_version"] == "2.0" and realkey["active"] == 2 and realkey["removed"] == 1 and realkey["last_run"] is None
    assert body["total_active"] == 2


async def test_public_health_announces_the_contract_version(client: AsyncClient):
    body = (await client.get("/health")).json()
    assert body["data"]["schema_version"] == "2.0"
