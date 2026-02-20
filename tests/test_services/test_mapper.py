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
    normalize_pearls_payload,
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
