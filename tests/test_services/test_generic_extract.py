"""Tests for the generic single-URL extraction pipeline."""
import pytest
from bs4 import BeautifulSoup

from app.services import generic_extract_service as ge
from app.services.mapper_service import _generic_address_from_raw, normalize_generic_payload


# ── normalize_generic_payload ───────────────────────────────────────────────

class TestNormalizeGenericPayload:
    def test_full_jsonld_style_payload(self):
        raw = {
            "url": "https://agencia-x.pt/imovel/apartamento-t3-porto",
            "title": "Apartamento T3 no centro do Porto",
            "price": "295 000 €",
            "area": "120 m²",
            "bedrooms": "3",
            "bathrooms": "2",
            "district": "Porto",
            "county": "Porto",
            "parish": "Cedofeita",
            "energy_certificate": "B",
            "images": ["https://cdn.x/1.jpg", "https://cdn.x/2.jpg"],
            "garage": "Yes",
            "raw_description": "Excelente apartamento renovado.",
        }
        schema = normalize_generic_payload(raw, "generic_agencia_x_pt")
        assert schema.title == "Apartamento T3 no centro do Porto"
        assert schema.price.amount == 295000.0
        assert schema.bedrooms == 3
        assert schema.bathrooms == 2
        assert schema.area_useful_m2 == 120.0
        assert schema.address.region == "Porto"
        assert schema.address.area == "Cedofeita"
        assert schema.property_type == "Apartamento"
        assert schema.features.has_garage is True
        assert len(schema.media) == 2
        assert schema.source_partner == "generic_agencia_x_pt"

    def test_infers_property_type_and_typology_from_title(self):
        raw = {"url": "https://x.pt/l/1", "title": "Moradia V4 com jardim", "price": "500000€"}
        schema = normalize_generic_payload(raw, "generic_x_pt")
        assert schema.property_type == "Moradia"
        assert schema.bedrooms == 4  # V4 → 4 via typology_to_bedrooms

    def test_strips_reference_label_from_partner_id(self):
        raw = {"url": "https://x.pt/l/1", "title": "Loja", "property_id": "Referência: 4471"}
        schema = normalize_generic_payload(raw, "generic_x_pt")
        assert schema.partner_id == "4471"


class TestGenericAddress:
    def test_explicit_parts_win(self):
        addr = _generic_address_from_raw(
            {"district": "Braga", "county": "Guimarães", "parish": "Creixomil"}
        )
        assert (addr.region, addr.city, addr.area) == ("Braga", "Guimarães", "Creixomil")

    def test_splits_comma_location_three_parts(self):
        addr = _generic_address_from_raw({"location": "Creixomil, Guimarães, Braga"})
        assert addr.region == "Braga"
        assert addr.city == "Guimarães"
        assert addr.area == "Creixomil"

    def test_splits_two_part_location(self):
        addr = _generic_address_from_raw({"location": "Guimarães, Braga"})
        assert addr.region == "Braga"
        assert addr.city == "Guimarães"

    def test_single_token_location(self):
        addr = _generic_address_from_raw({"location": "Lisboa"})
        assert addr.city == "Lisboa"


# ── helpers in generic_extract_service ─────────────────────────────────────

class TestChallengeDetection:
    @pytest.mark.parametrize("html", [
        "<html><head><title>Just a moment...</title></head><body>cf-browser-verification</body></html>",
        "<html><body>Please enable JavaScript and cookies to continue</body></html>",
        "<html>datadome</html>",
        "tiny",
    ])
    def test_flags_challenge_pages(self, html):
        assert ge._looks_like_challenge_page(html) is True

    def test_passes_real_page(self):
        html = "<html><body>" + ("<p>Apartamento T2 em Lisboa. " * 200) + "</body></html>"
        assert ge._looks_like_challenge_page(html) is False


class TestMappedSuggestedSelectors:
    def test_maps_and_filters_by_score(self):
        suggest_result = {
            "candidates": {
                "price": [{"selector": ".price", "score": 0.8}],
                "rooms": [{"selector": ".beds", "score": 0.7}],
                "bathrooms": [{"selector": ".baths", "score": 0.3}],  # below threshold
                "images": [{"selector": ".gallery img", "score": 0.9}],
                "district": [],
            }
        }
        mapped = ge._mapped_suggested_selectors(suggest_result)
        assert mapped["price_selector"] == ".price"
        assert mapped["bedrooms_selector"] == ".beds"
        assert mapped["image_selector"] == ".gallery img"
        assert "bathrooms_selector" not in mapped  # score too low
        assert "district_selector" not in mapped   # no candidates


class TestImageHeuristic:
    def test_pulls_largest_gallery_cluster(self):
        html = """
        <html><body>
          <img src="/logo.png">
          <div class="gallery">
            <img src="/photos/a.jpg"><img src="/photos/b.jpg"><img src="/photos/c.jpg">
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        raw = {"images": []}
        ge._image_heuristic(soup, "https://x.pt/l/1", raw)
        assert raw["images"] == [
            "https://x.pt/photos/a.jpg",
            "https://x.pt/photos/b.jpg",
            "https://x.pt/photos/c.jpg",
        ]

    def test_keeps_existing_when_already_enough(self):
        soup = BeautifulSoup("<html><body><div class='gallery'><img src='/x.jpg'></div></body></html>", "lxml")
        raw = {"images": ["a", "b", "c"]}
        ge._image_heuristic(soup, "https://x.pt/l/1", raw)
        assert raw["images"] == ["a", "b", "c"]


class TestPriceGuard:
    @pytest.mark.parametrize(("text", "ok"), [
        ("250 000", True), ("1.300.000", True), ("600", True),
        ("2026", False), ("1998", False), ("50", False),
    ])
    def test_rejects_years_and_tiny_numbers(self, text, ok):
        assert ge._plausible_price_text(text) is ok


class TestStripLabelValues:
    def test_nulls_out_bare_labels(self):
        raw = {"price": "Preço", "area": "Área útil", "typology": "T2", "condition": "Usado"}
        ge._strip_label_values(raw)
        assert "price" not in raw and "area" not in raw
        assert raw["typology"] == "T2" and raw["condition"] == "Usado"


class TestChromeTitle:
    @pytest.mark.parametrize(("title", "chrome"), [
        ("Detalhes de Imóvel LHA2000 :: Euro Estates", True),
        ("Apartamento T3 | Imobiliária X", True),
        (None, True),
        ("Moradia T3 Exclusiva No Centro de Sintra", False),
        ("Apartamento T1 - Meadela, Viana do Castelo", False),
    ])
    def test_detection(self, title, chrome):
        assert ge._title_looks_like_chrome(title) is chrome


class TestBusinessTypeFromText:
    def test_infers_rent_from_description(self):
        from app.services.mapper_service import _infer_business_type
        raw = {"title": "Apartamento T1", "raw_description": "O apartamento é arrendado sem mobília."}
        assert _infer_business_type(raw) == "rent"

    def test_defaults_to_sale(self):
        from app.services.mapper_service import _infer_business_type
        assert _infer_business_type({"title": "Moradia T4", "raw_description": "Excelente moradia."}) == "sale"


class TestTextHeuristics:
    def test_fills_missing_price_area_typology_energy(self):
        html = """
        <html><body><main>
          <h1>Apartamento em Aveiro</h1>
          <p>Preço: 210.000 € — Área útil 95 m². Certificado energético: classe C.</p>
        </main></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        raw = {"title": "Apartamento T2 em Aveiro"}
        ge._text_heuristics(soup, raw)
        assert "210.000" in raw["price"]
        assert "95" in raw["area"]
        assert raw["typology"] == "T2"
        assert raw["energy_certificate"] == "C"


# ── extract_generic end-to-end (fetch + suggester mocked) ──────────────────

_JSONLD_PAGE = """
<html><head>
<title>Apartamento T3 - Agencia Y</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"Apartamento T3 em Matosinhos",
 "offers":{"@type":"Offer","price":"320000","priceCurrency":"EUR"},
 "image":["https://cdn.y/p1.jpg","https://cdn.y/p2.jpg"]}
</script>
</head><body>
<main>
 <h1>Apartamento T3 em Matosinhos</h1>
 <p>3 quartos, 2 casas de banho. Área 110 m². Matosinhos, Porto.</p>
</main>
</body></html>
"""


@pytest.mark.asyncio
async def test_extract_generic_uses_structured_data(monkeypatch):
    async def fake_fetch(url):
        return _JSONLD_PAGE

    async def fake_suggest(url):
        return {"candidates": {}}

    monkeypatch.setattr(ge, "_fetch", fake_fetch)
    monkeypatch.setattr(ge, "suggest_selectors", fake_suggest)

    schema, provenance, raw = await ge.extract_generic("https://agencia-y.pt/imovel/apartamento-t3")

    assert schema.title == "Apartamento T3 em Matosinhos"
    assert schema.price.amount == 320000.0
    assert len(schema.media) == 2
    assert schema.source_partner == "generic_agencia_y_pt"
    assert provenance["title"] == "structured_data"
    assert provenance["price"] == "structured_data"
    assert raw["title"] == "Apartamento T3 em Matosinhos"
