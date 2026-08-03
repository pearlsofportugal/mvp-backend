"""Tests for Imodigi property_type / business_type payload mapping fixes."""

from app.models.listing_model import Listing
from app.services.imodigi_service import build_property_payload
from tests.conftest import make_listing_payload


class TestPropertyTypeMapping:
    def test_escritorio_maps_to_office_not_commercial(self):
        listing = Listing(**make_listing_payload(property_type="Escritório"))
        payload = build_property_payload(listing)
        assert payload["propertyType"] == "Office"

    def test_loja_still_maps_to_commercial(self):
        listing = Listing(**make_listing_payload(property_type="Loja"))
        payload = build_property_payload(listing)
        assert payload["propertyType"] == "Commercial"

    def test_lote_maps_to_lot_like_terreno(self):
        listing = Listing(**make_listing_payload(property_type="Lote"))
        payload = build_property_payload(listing)
        assert payload["propertyType"] == "Lot"

    def test_unconfirmed_type_passes_through_raw_value(self):
        listing = Listing(**make_listing_payload(property_type="Quinta"))
        payload = build_property_payload(listing)
        assert payload["propertyType"] == "Quinta"


class TestBusinessTypeMapping:
    def test_trespasse_does_not_fall_back_silently_to_to_buy(self):
        listing = Listing(**make_listing_payload(business_type="trespasse"))
        payload = build_property_payload(listing)
        assert payload["businessType"] == "Trespass"

    def test_sale_and_rent_unaffected(self):
        assert build_property_payload(
            Listing(**make_listing_payload(business_type="sale"))
        )["businessType"] == "To Buy"
        assert build_property_payload(
            Listing(**make_listing_payload(business_type="rent"))
        )["businessType"] == "To Rent"
