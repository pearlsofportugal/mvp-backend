"""Tests for AI enrichment API endpoints."""
import pytest
from httpx import AsyncClient

from app.schemas.ai_enrichment import (
    AIEnrichmentFieldResult,
    AIEnrichmentOutput,
    AIListingEnrichmentResponse,
    AITextOptimizationResponse,
)


@pytest.mark.asyncio
async def test_ai_optimize_text(client: AsyncClient, monkeypatch):
    """POST /api/v1/enrichment/ai/optimize returns AI output payload."""

    def fake_optimize(content: str, keywords):
        return AITextOptimizationResponse(
            model_used="fake-model",
            keywords_used=list(keywords),
            output=AIEnrichmentOutput(
                title="Título otimizado",
                description="Descrição otimizada",
                meta_description="Meta otimizada",
            ),
        )

    monkeypatch.setattr("app.api.v1.ai_enrichment.optimize_text_with_ai", fake_optimize)

    resp = await client.post(
        "/api/v1/enrichment/ai/optimize",
        json={
            "content": "Apartamento T2 em Lisboa com ótima localização.",
            "keywords": ["apartamento lisboa", "t2"],
            "fields": ["title", "description"],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["model_used"] == "fake-model"
    assert data["output"]["title"] == "Título otimizado"
    assert data["output"]["description"] == "Descrição otimizada"
    assert data["output"]["meta_description"] is None


@pytest.mark.asyncio
async def test_ai_listing_enrichment_apply(client: AsyncClient, monkeypatch):
    """POST /api/v1/enrichment/ai/listing enriches selected listing fields."""
    from tests.conftest import make_listing_payload

    created = await client.post("/api/v1/listings", json=make_listing_payload())
    listing_id = created.json()["data"]["id"]

    def fake_enrich_listing(listing, payload):
        return AIListingEnrichmentResponse(
            listing_id=listing.id,
            applied=payload.apply,
            model_used="fake-model",
            keywords_used=["apartamento lisboa"],
            results=[
                AIEnrichmentFieldResult(
                    field="title",
                    original=listing.title,
                    enriched="Novo título AI",
                    changed=True,
                )
            ],
        )

    monkeypatch.setattr("app.api.v1.ai_enrichment.enrich_listing_with_ai", fake_enrich_listing)

    resp = await client.post(
        "/api/v1/enrichment/ai/listing",
        json={
            "listing_id": listing_id,
            "fields": ["title"],
            "keywords": ["apartamento lisboa"],
            "apply": True,
            "force": True,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["listing_id"] == listing_id
    assert data["applied"] is True
    assert data["model_used"] == "fake-model"
    assert data["results"][0]["field"] == "title"
    assert data["results"][0]["enriched"] == "Novo título AI"
