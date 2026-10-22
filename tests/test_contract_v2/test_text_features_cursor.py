"""Text rules, amenity extraction and the cursor."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.contract_v2.cursor import InvalidCursor, decode_cursor, encode_cursor
from app.contract_v2.features import FEATURE_LABELS, extract_features
from app.contract_v2.media import serialize_media, thumb_url
from app.contract_v2.quality import assess
from app.contract_v2.text import clean_text, has_contact, make_description, make_title, slugify, strip_contacts


# ── text ────────────────────────────────────────────────────────────────────────────────────────────

def test_clean_text_removes_tags_entities_and_extra_blank_lines():
    assert clean_text("<p>Casa&nbsp;com <b>jardim</b></p>\n\n\n\nFim") == "Casa com jardim\n\nFim"
    assert clean_text(None) == ""


@pytest.mark.parametrize("text", [
    "Ligue 912345678", "Tel. +351 912 345 678", "contacto: 223 456 789", "escreva para a.b@agencia.pt", "00351 961 234 567",
])
def test_contacts_are_detected(text):
    assert has_contact(text) is True


@pytest.mark.parametrize("text", ["Preço 1.250.000 euros", "Área de 1250 m2", "T2 com 90 m2", "Referência 12345"])
def test_prices_areas_and_references_are_not_contacts(text):
    assert has_contact(text) is False and strip_contacts(text) == text


def test_strip_contacts_removes_numbers_and_emails_only():
    assert strip_contacts("Ligue 912 345 678 ou a@b.pt para visitar.") == "Ligue ou para visitar."


def test_title_is_shortened_on_a_word_boundary_and_never_exceeds_the_limit():
    title = make_title("palavra " * 30, "fallback")
    assert len(title) <= 120 and title.endswith("…") and "  " not in title


def test_short_title_uses_the_fallback_and_description_is_padded_to_forty():
    assert make_title("T2", "Apartamento T2 em Porto") == "Apartamento T2 em Porto"
    text, synthesised = make_description("curto", "Apartamento T2 em Porto.")
    assert synthesised is True and len(text) >= 40
    long_text, synthesised = make_description("x" * 50, "irrelevante")
    assert long_text == "x" * 50 and synthesised is False


def test_slugify_drops_accents_and_punctuation():
    assert slugify("Paços de Ferreira") == "pacos-de-ferreira"
    assert slugify("União das freguesias de A, B e C") == "uniao-das-freguesias-de-a-b-e-c"
    assert slugify("") is None and slugify(None) is None


# ── features ────────────────────────────────────────────────────────────────────────────────────────

def _flags(**kw):
    base = dict(has_garage=None, has_elevator=None, has_balcony=None, has_air_conditioning=None, has_pool=None, has_garden=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_features_come_from_flags_and_from_clear_text():
    out = extract_features(_flags(has_garage=True), "Cozinha equipada, lareira, vista mar e churrasqueira. Vidros duplos.")
    assert out == sorted(["garage", "equipped_kitchen", "fireplace", "sea_view", "barbecue", "double_glazing"])


def test_negations_and_hedges_do_not_create_features():
    text = "Sem garagem. Perto de uma piscina municipal. Possibilidade de ar condicionado. Não mobilado."
    assert extract_features(_flags(), text) == []


def test_a_view_mention_does_not_swallow_the_amenities_that_follow_it():
    text = "Vista mar e churrasqueira. Vidros duplos. Sem lareira."
    assert extract_features(_flags(), text) == ["barbecue", "double_glazing", "sea_view"]


def test_a_stored_false_beats_a_loose_text_match():
    assert "garage" not in extract_features(_flags(has_garage=False), "Apartamento com garagem")


def test_every_code_has_portuguese_and_english_labels():
    assert len(FEATURE_LABELS) == 26 and all(pt and en for pt, en in FEATURE_LABELS.values())


# ── media ───────────────────────────────────────────────────────────────────────────────────────────

def test_thumbnail_rules_by_host():
    assert thumb_url("https://imgs.centralimo.pt/1/properties/ab-cd.jpg") == "https://imgs.centralimo.pt/1/properties/ab-cd_s.jpg"
    assert thumb_url("https://images.egorealestate.com/Z1280x960/S5/P1/Tphoto/ID1.jpg") == "https://images.egorealestate.com/Z320x240/S5/P1/Tphoto/ID1.jpg"
    assert thumb_url("https://example.com/a.jpg") is None


def test_only_the_first_photo_is_the_cover_even_when_it_is_not_at_position_zero():
    assets = [
        SimpleNamespace(id=uuid4(), url="https://x/plan.jpg", type="floorplan", position=0, width=None, height=None, alt_text=None),
        SimpleNamespace(id=uuid4(), url="https://x/a.jpg", type="photo", position=1, width=None, height=None, alt_text=None),
        SimpleNamespace(id=uuid4(), url="https://x/b.jpg", type="photo", position=2, width=None, height=None, alt_text=None),
    ]
    covers = [m["is_cover"] for m in serialize_media(assets)]
    assert covers == [False, True, False]


# ── quality ─────────────────────────────────────────────────────────────────────────────────────────

def _assess(**kw):
    base = dict(photo_count=10, widths=[], watermarked=False, price_ok=True, area_ok=True, description_synthesised=False,
                had_contact=False, address_matched=True, features=["garage"], place_ok=True, active=True)
    base.update(kw)
    return assess(**base)


def test_perfect_listing_scores_100_and_is_publishable():
    assert _assess() == {"score": 100, "issues": [], "publishable": True}


def test_issues_cost_points_and_block_publication_when_essential():
    q = _assess(photo_count=0, price_ok=False)
    assert q["issues"] == ["no_photos", "price_missing"] and q["score"] == 35 and q["publishable"] is False
    assert _assess(photo_count=3)["issues"] == ["few_photos"] and _assess(photo_count=3)["publishable"] is True
    assert _assess(widths=[400, 1024])["issues"] == ["low_res_photos"]
    assert _assess(active=False)["publishable"] is False


# ── cursor ──────────────────────────────────────────────────────────────────────────────────────────

def test_cursor_round_trips_and_is_opaque():
    when, ident = datetime(2026, 10, 22, 12, 30, 15, 123456, tzinfo=timezone.utc), uuid4()
    cursor = encode_cursor(when, ident)
    assert decode_cursor(cursor) == (when, ident) and "=" not in cursor and str(ident) not in cursor


@pytest.mark.parametrize("bad", ["", "abc", "e30", "eyJ1IjoibmFvIn0", "!!!"])
def test_invalid_cursors_are_rejected(bad):
    with pytest.raises(InvalidCursor):
        decode_cursor(bad)
