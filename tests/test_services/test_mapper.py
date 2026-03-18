"""Tests for mapper service — price parsing, area parsing, normalization."""
import pytest
from decimal import Decimal

from app.services.mapper_service import (
    parse_price,
    parse_area,
    parse_int,
    parse_bool,
    typology_to_bedrooms,
    calculate_price_per_m2,
    _normalize_description_text,
    normalize_habinedita_payload,
    normalize_pearls_payload,
    schema_to_listing_dict,
)


class TestParsePrice:
    def test_standard_euro(self):
        amount, currency = parse_price("250 000 €")
        assert amount == Decimal("250000")
        assert currency == "EUR"

    def test_european_format(self):
        amount, currency = parse_price("1.250.000 €")
        assert amount == Decimal("1250000")
        assert currency == "EUR"

    def test_with_decimals(self):
        amount, currency = parse_price("250.000,50 €")
        assert amount == Decimal("250000.50")

    def test_none_input(self):
        assert parse_price(None) == (None, None)

    def test_no_number(self):
        assert parse_price("Price on request") == (None, None)

    def test_usd(self):
        amount, currency = parse_price("$500,000")
        assert amount == Decimal("500000")
        assert currency == "USD"


class TestParseArea:
    def test_standard_m2(self):
        assert parse_area("120 m²") == 120.0

    def test_with_comma(self):
        assert parse_area("120,5 m²") == 120.5

    def test_none_input(self):
        assert parse_area(None) is None

    def test_no_number(self):
        assert parse_area("not available") is None


class TestParseInt:
    def test_from_string(self):
        assert parse_int("2") == 2

    def test_from_typology(self):
        assert parse_int("T3") == 3

    def test_none(self):
        assert parse_int(None) is None


class TestParseBool:
    def test_yes(self):
        assert parse_bool("Yes") is True

    def test_sim(self):
        assert parse_bool("Sim") is True

    def test_no(self):
        assert parse_bool("No") is False

    def test_none(self):
        assert parse_bool(None) is None


class TestTypologyToBedrooms:
    def test_t3(self):
        assert typology_to_bedrooms("T3") == 3

    def test_t0(self):
        assert typology_to_bedrooms("T0") == 0

    def test_none(self):
        assert typology_to_bedrooms(None) is None


class TestCalculatePricePerM2:
    def test_calculation(self):
        result = calculate_price_per_m2(Decimal("250000"), 100.0)
        assert result == Decimal("2500.00")

    def test_zero_area(self):
        assert calculate_price_per_m2(Decimal("250000"), 0) is None

    def test_none_price(self):
        assert calculate_price_per_m2(None, 100.0) is None


class TestNormalizeDescriptionText:
    def test_strips_leading_description_label_and_normalizes_spacing(self):
        raw = "DescriçãoS. Pedro da Cova.Gondomar. Moradia V3 com garagem para 2 carros."

        normalized = _normalize_description_text(raw)

        assert normalized == "S. Pedro da Cova. Gondomar. Moradia V3 com garagem para 2 carros."


class TestNormalizePearlsPayload:
    def test_full_normalization(self):
        raw = {
            "url": "https://example.com/property/123",
            "title": "Beautiful Apartment T2",
            "property_id": "REF-123",
            "price": "250 000 €",
            "property_type": "Apartment",
            "typology": "T2",
            "bathrooms": "1",
            "useful_area": "80 m²",
            "gross_area": "100 m²",
            "district": "Lisboa",
            "county": "Lisboa",
            "parish": "Estrela",
            "garage": "Yes",
            "elevator": "Yes",
            "swimming_pool": None,
            "energy_certificate": "B",
            "construction_year": "2015",
            "raw_description": "A nice apartment.",
            "images": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
            "alt_texts": ["Photo 1", "Photo 2"],
        }

        schema = normalize_pearls_payload(raw)
        assert schema.source_partner == "pearls"
        assert schema.title == "Beautiful Apartment T2"
        assert schema.price.amount == 250000.0
        assert schema.price.currency == "EUR"
        assert schema.bedrooms == 2  # From T2
        assert schema.bathrooms == 1
        assert schema.area_useful_m2 == 80.0
        assert schema.features.has_garage is True
        assert schema.features.has_elevator is True
        assert schema.features.has_pool is None
        assert len(schema.media) == 2
        assert schema.address.region == "Lisboa"
        assert schema.descriptions["raw"] == "A nice apartment."
        assert schema.descriptions["pt"] == "A nice apartment."


class TestNormalizeHabineditaPayload:
    def test_maps_land_area_and_seo_fields(self):
        raw = {
            "url": "https://habinedita.example/imovel/1",
            "property_id": "HB123",
            "title": "Moradia em Banda T3",
            "price": "517 500 €",
            "business_type": "Venda",
            "property_type": "Moradia em Banda",
            "typology": "T3",
            "bathrooms": "5",
            "useful_area": "185 m²",
            "gross_area": "268 m²",
            "land_area": "185 m²",
            "district": "Porto",
            "county": "Gondomar",
            "parish": "Fânzeres e São Pedro da Cova",
            "condition": "Novo",
            "energy_certificate": "A",
            "raw_description": "DescriçãoMoradia nova com garagem e jardim.",
            "page_title": "Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HB123",
            "meta_description": "Moradia nova em Gondomar.",
            "headers": [{"level": "h1", "text": "Moradia em Banda T3"}],
            "images": ["https://example.com/1.jpg"],
            "alt_texts": ["Frente"],
            "contacts": "mail@example.com 912345678",
            "advertiser": "Pedro Sousa",
        }

        schema = normalize_habinedita_payload(raw)
        listing_data = schema_to_listing_dict(schema)

        assert schema.source_partner == "habinedita"
        assert schema.area_land_m2 == 185.0
        assert schema.energy_certificate == "A"
        assert schema.descriptions["raw"] == "DescriçãoMoradia nova com garagem e jardim."
        assert schema.descriptions["pt"] == "Moradia nova com garagem e jardim."
        assert schema.seo == {
            "page_title": "Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HB123",
            "meta_description": "Moradia nova em Gondomar.",
            "headers": [{"level": "h1", "text": "Moradia em Banda T3"}],
        }
        assert schema.features.is_new_construction is True
        assert listing_data["area_land_m2"] == 185.0
        assert listing_data["raw_description"] == "DescriçãoMoradia nova com garagem e jardim."
        assert listing_data["description"] == "Moradia nova com garagem e jardim."
        assert listing_data["page_title"] == "Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HB123"
        assert listing_data["meta_description"] == "Moradia nova em Gondomar."
        assert listing_data["headers"] == [{"level": "h1", "text": "Moradia em Banda T3"}]
