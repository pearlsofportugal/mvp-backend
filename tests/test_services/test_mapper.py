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
    normalize_imo_atlantico_payload,
    normalize_bpaproperty_payload,
    normalize_partner_payload,
    schema_to_listing_dict,
    missing_critical_schema_fields,
    _infer_business_type,
)


from app.core.normalizer import normalize_energy_certificate


class TestNormalizeEnergyCertificate:
    @pytest.mark.parametrize(("raw_value", "property_type", "expected"), [
        (None, "Terreno", "Exempted"),
        (None, "Lote de terreno", "Exempted"),
        ("A+", "Apartamento", "A+"),
        ("B -", "Moradia", "B-"),
        ("Isento", "Moradia", "Exempted"),
        ("N/A", "Moradia", "Unavailable"),
        (None, "Moradia", "Unavailable"),
    ])
    def test_normalizes_ratings_and_missing_certificates(self, raw_value, property_type, expected):
        assert normalize_energy_certificate(raw_value, property_type) == expected


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
        assert parse_price("Price on request") == (Decimal("-1"), None)

    def test_usd(self):
        amount, currency = parse_price("$500,000")
        assert amount == Decimal("500000")
        assert currency == "USD"

    def test_sale_price_below_1000_is_rejected_as_implausible(self):
        assert parse_price("850 EUR", business_type="sale") == (None, None)

    def test_rent_price_below_1000_is_accepted(self):
        amount, currency = parse_price("850 EUR", business_type="rent")
        assert amount == Decimal("850")
        assert currency == "EUR"

    def test_rent_price_still_rejects_noise_below_floor(self):
        # e.g. a stray "3" picked up from "3 quartos" — must not become a price.
        assert parse_price("3", business_type="rent") == (None, None)


class TestParseArea:
    def test_standard_m2(self):
        assert parse_area("120 m²") == 120.0

    def test_with_comma(self):
        assert parse_area("120,5 m²") == 120.5

    def test_none_input(self):
        assert parse_area(None) is None

    def test_no_number(self):
        assert parse_area("not available") is None

    def test_european_thousands_with_decimal_comma(self):
        assert parse_area("1.500,50 m²") == 1500.50

    def test_american_thousands_with_decimal_point(self):
        assert parse_area("1,234.56 m²") == 1234.56

    def test_thousands_only_dot_separator(self):
        assert parse_area("2.408 m²") == 2408.0


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

    def test_does_not_match_letter_sequence_inside_a_word(self):
        # "HDTV5" contains "TV5" — must not be read as typology T/V + digits.
        assert typology_to_bedrooms("HDTV5") is None

    def test_still_matches_typology_token_within_a_title(self):
        assert typology_to_bedrooms("Apartamento T3 Bonito") == 3


class TestInferBusinessType:
    def test_detects_trespasse_keyword(self):
        assert _infer_business_type({"business_type": "Trespasse"}) == "trespasse"

    def test_detects_rent_keyword(self):
        assert _infer_business_type({"business_type": "Arrendamento"}) == "rent"

    def test_defaults_to_sale(self):
        assert _infer_business_type({"business_type": "Venda"}) == "sale"

    def test_trespasse_is_not_shadowed_by_rent_url_hint(self):
        assert _infer_business_type(
            {"business_type": "Trespasse"}, url_hint="https://x.pt/Imovel/Arrendamento/Loja/1"
        ) == "trespasse"

    def test_trespasse_url_hint_without_explicit_field(self):
        assert _infer_business_type({}, url_hint="https://x.pt/Imovel/Trespasse/Loja/1") == "trespasse"


class TestHasGardenWiring:
    def test_garden_raw_field_reaches_the_schema(self):
        schema = normalize_pearls_payload({
            "url": "https://example.com/1",
            "title": "Moradia com jardim",
            "property_type": "Moradia",
            "garden": "Yes",
        })
        assert schema.features.has_garden is True

        listing_data = schema_to_listing_dict(schema)
        assert listing_data["has_garden"] is True


class TestPriceOnRequest:
    def test_sold_price_on_request_is_flagged_and_amount_is_none(self):
        schema = normalize_partner_payload(
            {
                "url": "https://example.com/1",
                "title": "Moradia com vista",
                "price": "Sob consulta",
                "property_type": "Moradia",
            },
            "pearls",
        )
        assert schema.price_on_request is True
        assert schema.price.amount is None


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
        assert schema.condition == "Novo"
        assert listing_data["condition"] == "Novo"
        assert listing_data["area_land_m2"] == 185.0
        assert listing_data["raw_description"] == "DescriçãoMoradia nova com garagem e jardim."
        assert listing_data["description"] == "Moradia nova com garagem e jardim."
        assert listing_data["page_title"] == "Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HB123"
        assert listing_data["meta_description"] == "Moradia nova em Gondomar."
        assert listing_data["headers"] == [{"level": "h1", "text": "Moradia em Banda T3"}]


class TestNormalizeImoAtlanticoPayload:
    def test_maps_sale_fields_from_detail_payload(self):
        raw = {
            "url": "https://www.imo-atlantico.pt/imovel-venda-moradia-t3-camara-de-lobos-6218077",
            "title": "EMPREENDIMENTO - MORADIA E",
            "price": "850.000€",
            "business_type": "Venda",
            "property_type": "Moradia",
            "typology": "T3",
            "bathrooms": "2",
            "useful_area": "178.15 m²",
            "gross_area": "237.5 m²",
            "land_area": "368.97 m²",
            "district": "Madeira",
            "county": "Câmara de Lobos",
            "parish": "Câmara de Lobos",
            "raw_description": "Moradia nova com excelente exposição solar.",
            "images": ["https://example.com/photo.jpg"],
            "alt_texts": ["Frente"],
        }

        schema = normalize_imo_atlantico_payload(raw)

        assert schema.source_partner == "imo_atlantico"
        assert schema.title == "EMPREENDIMENTO - MORADIA E"
        assert schema.business_type == "sale"
        assert schema.property_type == "Moradia"
        assert schema.bedrooms == 3
        assert schema.bathrooms == 2
        assert schema.price.amount == 850000.0
        assert schema.price.currency == "EUR"
        assert schema.area_useful_m2 == 178.15
        assert schema.area_gross_m2 == 237.5
        assert schema.area_land_m2 == 368.97
        assert schema.address.region == "Madeira"
        assert schema.address.city == "Câmara de Lobos"
        assert schema.address.area == "Câmara de Lobos"
        assert schema.descriptions["pt"] == "Moradia nova com excelente exposição solar."


class TestNormalizeBpapropertyPayload:
    def test_maps_fields_from_direct_selector_and_text_pattern_output(self):
        raw = {
            "url": "https://www.bpaproperty.com/en/property/townhouse/lagos-6132/?reference=BPA5646",
            "title": "3 Bedroom Townhouse in Boavista Golf and Leisure Resort, Lagos",
            "location": "Boavista Golf and Leisure Resort, Lagos",
            "bedrooms": "3 Beds",
            "bathrooms": "3 Bath",
            "property_id": "Ref: BPA5646",
            "energy_certificate": "B-",
            "gross_area": "217.00 m",
            "land_area": "264.00 m",
            "construction_year": "2008",
            "condition": "Used",
            "price": "849.000",
            "raw_description": "This beautifully presented 3-bedroom fully renovated semi-detached townhouse.",
            "garage": "Yes",
            "balcony": "Yes",
            "swimming_pool": "Yes",
            "garden": "Yes",
            "images": ["https://crm.bpaproperty.com/media/clients/1/properties/6132/medium/1.jpg"],
            "alt_texts": [""],
        }

        schema = normalize_bpaproperty_payload(raw)

        assert schema.source_partner == "bpaproperty"
        assert schema.business_type == "sale"
        assert schema.property_type == "Townhouse"
        assert schema.partner_id == "BPA5646"
        assert schema.condition == "Used"
        assert schema.address.region == "Faro"
        assert schema.address.city == "Lagos"
        assert schema.address.area == "Boavista Golf and Leisure Resort"
        assert schema.bedrooms == 3
        assert schema.bathrooms == 3
        assert schema.area_gross_m2 == 217.0
        assert schema.area_land_m2 == 264.0
        assert schema.construction_year == 2008
        assert schema.energy_certificate == "B-"
        assert schema.price.amount == 849000.0
        assert schema.features.has_garage is True
        assert schema.features.has_balcony is True
        assert schema.features.has_pool is True
        assert schema.features.has_garden is True

    def test_property_type_falls_back_to_url_slug_for_non_residential_types(self):
        raw = {
            "url": "https://www.bpaproperty.com/en/property/plot/lagos-7000/?reference=BPA9999",
            "title": "Building Plot in Lagos",
            "location": "Lagos",
            "property_id": "Ref: BPA9999",
        }

        schema = normalize_bpaproperty_payload(raw)

        assert schema.property_type == "Plot"
        assert schema.bedrooms is None
        assert schema.address.city == "Lagos"
        assert schema.address.area is None


class TestMissingCriticalSchemaFields:
    """These must be checked against the normalized schema, not the raw
    parser dict — bpaproperty (and realkey/EGO-platform partners) derive
    property_type/district purely in the mapper (URL parsing, fixed
    constants), so they'd never appear in raw_data even when correct."""

    def test_bpaproperty_style_payload_has_no_missing_fields(self):
        raw = {
            "url": "https://www.bpaproperty.com/en/property/townhouse/lagos-6132/?reference=BPA5646",
            "title": "3 Bedroom Townhouse in Boavista Golf and Leisure Resort, Lagos",
            "location": "Boavista Golf and Leisure Resort, Lagos",
            "price": "849.000",
        }
        schema = normalize_bpaproperty_payload(raw)

        assert missing_critical_schema_fields(schema) == []

    def test_reports_missing_property_type_and_district(self):
        schema = normalize_pearls_payload({
            "url": "https://example.com/1",
            "title": "Nice place",
            "price": "300 000 €",
        })

        assert sorted(missing_critical_schema_fields(schema)) == ["district", "property_type"]

    def test_price_on_request_is_not_reported_as_missing(self):
        schema = normalize_pearls_payload({
            "url": "https://example.com/1",
            "title": "Nice place",
            "price": "Sob consulta",
            "property_type": "Moradia",
            "district": "Lisboa",
        })

        assert schema.price_on_request is True
        assert "price" not in missing_critical_schema_fields(schema)

    def test_all_fields_present_reports_nothing(self):
        schema = normalize_pearls_payload({
            "url": "https://example.com/1",
            "title": "Nice place",
            "price": "300 000 €",
            "property_type": "Moradia",
            "district": "Lisboa",
        })

        assert missing_critical_schema_fields(schema) == []
