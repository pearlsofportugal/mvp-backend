"""Tests for the /api/v1/ingest endpoint."""
import pytest

from app.schemas.property_schema import Address, Money, PropertySchema
from app.services import ingest_service


def _fake_schema(**overrides) -> PropertySchema:
    data = dict(
        source_partner="generic_agencia_z_pt",
        source_url="https://agencia-z.pt/imovel/1",
        title="Apartamento T2 em Faro",
        property_type="Apartamento",
        bedrooms=2,
        price=Money(amount=180000.0, currency="EUR"),
        address=Address(country="Portugal", region="Faro", city="Faro", area="Sé"),
    )
    data.update(overrides)
    return PropertySchema(**data)


@pytest.mark.asyncio
async def test_ingest_generic_success(client, monkeypatch):
    async def fake_ingest_generic(url):
        return (
            _fake_schema(),
            {"title": "structured_data", "price": "structured_data"},
            {"title": "Apartamento T2 em Faro", "price": "180000 €"},
        )

    async def fake_match_site(db, url):
        return None

    monkeypatch.setattr(ingest_service, "extract_generic", fake_ingest_generic)
    monkeypatch.setattr(ingest_service, "_match_site_config", fake_match_site)

    resp = await client.post("/api/v1/ingest", json={"url": "https://agencia-z.pt/imovel/1"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["success"] is True
    assert body["source"] == "generic"
    assert body["property"]["title"] == "Apartamento T2 em Faro"
    assert body["property"]["price"]["amount"] == 180000.0
    assert "title" in body["completeness"]["populated"]
    assert "bathrooms" in body["completeness"]["missing"]
    assert body["field_provenance"]["price"] == "structured_data"


@pytest.mark.asyncio
async def test_ingest_reports_extraction_failure(client, monkeypatch):
    from app.services.generic_extract_service import GenericExtractError

    async def fake_ingest_generic(url):
        raise GenericExtractError("This site is protected against automated access.")

    async def fake_match_site(db, url):
        return None

    monkeypatch.setattr(ingest_service, "extract_generic", fake_ingest_generic)
    monkeypatch.setattr(ingest_service, "_match_site_config", fake_match_site)

    resp = await client.post("/api/v1/ingest", json={"url": "https://idealista.pt/imovel/1"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["success"] is False
    assert "protected" in body["error"]


@pytest.mark.asyncio
async def test_ingest_rejects_ssrf(client):
    resp = await client.post("/api/v1/ingest", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert resp.status_code == 422
