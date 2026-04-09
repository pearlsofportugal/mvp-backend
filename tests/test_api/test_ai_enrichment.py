"""Tests for AI enrichment API endpoints."""
from httpx import AsyncClient

from app.schemas.ai_enrichment_schema import (
    ListingTranslationResponse,
    LocaleEnrichmentOutput,
)


async def test_translations_preview(client: AsyncClient, monkeypatch):
    """POST /api/v1/enrichment/ai/translations returns multi-locale preview."""
    from tests.conftest import make_listing_payload
    from uuid import UUID

    created = await client.post("/api/v1/listings", json=make_listing_payload())
    listing_id = created.json()["data"]["id"]

    async def fake_enrich(db, lid, payload):
        return ListingTranslationResponse(
            listing_id=lid,
            applied=False,
            model_used="fake-model",
            keywords_used=["t2", "lisboa"],
            locales_generated=["en", "pt"],
            locales_cached=[],
            results={
                "en": LocaleEnrichmentOutput(title="EN title", description="EN desc", meta_description="EN meta"),
                "pt": LocaleEnrichmentOutput(title="PT título", description="PT desc", meta_description="PT meta"),
            },
        )

    monkeypatch.setattr("app.api.v1.ai_enrichment.enrich_translations_and_persist", fake_enrich)

    resp = await client.post(
        "/api/v1/enrichment/ai/translations",
        json={"listing_id": listing_id, "locales": ["en", "pt"], "apply": False},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["listing_id"] == listing_id
    assert data["applied"] is False
    assert data["model_used"] == "fake-model"
    assert data["results"]["en"]["title"] == "EN title"
    assert data["results"]["pt"]["title"] == "PT título"
    assert "en" in data["locales_generated"]


async def test_translations_apply(client: AsyncClient, monkeypatch):
    """POST /api/v1/enrichment/ai/translations with apply=true persists values."""
    from tests.conftest import make_listing_payload

    created = await client.post("/api/v1/listings", json=make_listing_payload())
    listing_id = created.json()["data"]["id"]

    async def fake_enrich(db, lid, payload):
        return ListingTranslationResponse(
            listing_id=lid,
            applied=True,
            model_used="fake-model",
            keywords_used=[],
            locales_generated=["en"],
            locales_cached=[],
            results={"en": LocaleEnrichmentOutput(title="Saved EN", description="d", meta_description="m")},
        )

    monkeypatch.setattr("app.api.v1.ai_enrichment.enrich_translations_and_persist", fake_enrich)

    resp = await client.post(
        "/api/v1/enrichment/ai/translations",
        json={
            "listing_id": listing_id,
            "locales": ["en"],
            "apply": True,
            "translation_values": {
                "en": {"title": "Saved EN", "description": "d", "meta_description": "m"}
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["applied"] is True
    assert data["results"]["en"]["title"] == "Saved EN"
