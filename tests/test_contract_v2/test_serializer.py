"""Every serialised listing must satisfy the JSON Schema of the contract, one case per partner and the edge cases."""
import json
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest

from app.contract_v2 import to_contract
from tests.test_contract_v2.fixtures import LONG_PT, NOW, PARTNER, make_listing, media, partner_listings

CONTRACTS = Path(__file__).resolve().parents[2] / "app" / "contracts"
SCHEMA = json.loads((CONTRACTS / "listing-2.0.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())


def violations(obj) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(obj)]


def test_schema_itself_is_valid_and_the_example_of_the_contract_passes():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    example = json.loads((CONTRACTS / "listing-2.0.example.json").read_text(encoding="utf-8"))
    assert violations(example) == []


@pytest.mark.parametrize("key", sorted(partner_listings()))
def test_one_listing_per_partner_satisfies_the_schema(key):
    out = to_contract(partner_listings()[key], {**PARTNER, "key": key})
    assert violations(out) == []
    assert out["source"]["partner_key"] == key
    assert out["schema_version"] == "2.0"


def test_underscored_partner_keys_are_accepted():
    out = to_contract(partner_listings()["brightmangroup_pt"])
    assert out["source"]["partner_key"] == "brightmangroup_pt" and violations(out) == []


def test_realistic_listing_is_complete_and_publishable():
    out = to_contract(make_listing(), PARTNER)
    assert out["status"] == "active" and out["removed_at"] is None and out["removed_reason"] is None
    assert out["property_type"]["code"] == "apartment"
    assert out["typology"] == {"label": "T2", "main": 2, "extra": 0}
    assert out["location"]["parish"] == {"code": "131704", "name": "Canidelo", "slug": "canidelo"}
    assert out["location"]["county"]["name"] == "Vila Nova de Gaia" and out["location"]["district"]["code"] == "13"
    assert out["location"]["display_address"] == "Canidelo, Vila Nova de Gaia"
    assert out["price"]["amount"] == 245000 and out["price"]["on_request"] is False and out["price"]["period"] is None
    assert {"garage", "elevator", "equipped_kitchen", "double_glazing", "central_heating"} <= set(out["features"])
    assert out["building"]["energy_class"] == "C" and out["condition"] == "used"
    assert out["content"]["default_language"] == "pt" and out["quality"]["publishable"] is True
    assert out["content_hash"] == "sha256:" + "a" * 16


def test_media_has_a_cover_thumbnails_and_positions():
    out = to_contract(make_listing())
    items = out["media"]
    assert [m["position"] for m in items] == list(range(len(items)))
    assert [m["is_cover"] for m in items] == [True] + [False] * (len(items) - 1)
    assert items[0]["thumb_url"].endswith("_s.jpg") and "_s" not in items[0]["url"]
    assert items[0]["mime"] == "image/jpeg"


def test_ego_gallery_gets_a_small_thumbnail_and_watermark_is_flagged():
    ego = to_contract(partner_listings()["sunpoint"])
    assert "/Z320x240/" in ego["media"][0]["thumb_url"] and "/Z1280x960/" in ego["media"][0]["url"]
    wm = to_contract(partner_listings()["imoantlantico"])
    assert all(m["has_watermark"] is True for m in wm["media"]) and "watermarked_photos" in wm["quality"]["issues"]


def test_rent_has_a_monthly_period_and_price_on_request_has_no_amount():
    rent = to_contract(make_listing(business_type="rent", price_amount=1200))
    assert rent["price"]["period"] == "month" and rent["price"]["amount"] == 1200
    on_request = to_contract(partner_listings()["imoantlantico"])
    assert on_request["price"] == {**on_request["price"], "amount": None, "on_request": True}
    assert "price_missing" not in on_request["quality"]["issues"] and violations(on_request) == []


def test_previous_price_comes_from_the_price_history():
    from types import SimpleNamespace

    history = [
        SimpleNamespace(price_amount=260000, recorded_at=NOW - timedelta(days=30)),
        SimpleNamespace(price_amount=250000, recorded_at=NOW - timedelta(days=10)),
    ]
    out = to_contract(make_listing(price_history=history))
    assert out["price"]["previous_amount"] == 250000


def test_removed_listing_keeps_its_data_and_says_why():
    out = to_contract(make_listing(status="removed", removed_at=NOW))
    assert out["status"] == "removed" and out["removed_reason"] == "not_found" and out["removed_at"].endswith("Z")
    assert out["quality"]["publishable"] is False and violations(out) == []


def test_listing_without_photos_is_reported_not_broken():
    out = to_contract(make_listing(media_assets=[]))
    assert out["media"] == []
    assert "no_photos" in out["quality"]["issues"] and out["quality"]["publishable"] is False
    assert violations(out) == []


def test_text_rules_title_description_and_contacts():
    long_title = "Excelente apartamento T2 completamente remodelado " * 4
    out = to_contract(make_listing(
        title=long_title,
        description_clean="Ligue 912 345 678 ou escreva para agente@agencia.pt. " + LONG_PT,
    ))
    pt = out["content"]["translations"]["pt"]
    assert 5 <= len(pt["title"]) <= 120 and pt["title"].endswith("…")
    assert "912" not in pt["description"] and "@" not in pt["description"]
    assert "contact_in_text" in out["quality"]["issues"] and violations(out) == []


def test_short_title_and_description_are_synthesised_from_structured_data():
    out = to_contract(make_listing(title="T2", description_clean=None, description=None, raw_description="curto"))
    pt = out["content"]["translations"]["pt"]
    assert pt["title"] == "Apartamento T2 em Canidelo"
    assert len(pt["description"]) >= 40 and "Canidelo" in pt["description"]
    assert "description_short" in out["quality"]["issues"] and out["quality"]["publishable"] is False
    assert violations(out) == []


def test_ai_translations_are_used_when_valid_and_dropped_when_too_short():
    ai = {
        "en": {"title": "2-bedroom apartment in Canidelo", "description": "A bright two-bedroom apartment in Canidelo, close to everything.", "meta_description": "2-bed flat"},
        "pt": {"title": "Apartamento T2 em Canidelo", "description": "Apartamento T2 luminoso em Canidelo, junto a tudo o que precisa no dia a dia.", "meta_description": None},
        "es": {"title": "Apartamento", "description": "corto"},
    }
    out = to_contract(make_listing(enriched_translations=ai))
    assert list(out["content"]["translations"]) == ["pt", "en"]
    assert out["content"]["is_enriched"] is True and "description_short" not in out["quality"]["issues"]
    assert violations(out) == []


def test_unmatched_address_is_flagged_and_still_valid():
    out = to_contract(make_listing(district_code=None, county_code=None, parish_code=None, district="Porto", county="Gaia", parish=None))
    assert "address_unmatched" in out["quality"]["issues"]
    assert out["location"]["county"] == {"code": None, "name": "Gaia", "slug": "gaia"} and violations(out) == []


def test_missing_place_never_breaks_the_schema():
    out = to_contract(make_listing(district=None, county=None, parish=None, district_code=None, county_code=None, parish_code=None))
    assert violations(out) == [] and out["quality"]["publishable"] is False


def test_out_of_range_numbers_become_null_instead_of_violating_the_schema():
    out = to_contract(make_listing(bedrooms=99, bathrooms=-1, construction_year=2200))
    assert out["rooms"]["bedrooms"] is None and out["rooms"]["bathrooms"] is None and out["building"]["construction_year"] is None
    assert violations(out) == []


def test_more_than_sixty_photos_are_cut_to_the_contract_maximum():
    out = to_contract(make_listing(media_assets=media([f"https://imgs.centralimo.pt/1/properties/{i:08x}-0000-0000-0000-000000000000.jpg" for i in range(1, 94)])))
    assert len(out["media"]) == 60 and violations(out) == []


def test_output_is_json_serialisable():
    json.dumps(to_contract(make_listing(), PARTNER))
