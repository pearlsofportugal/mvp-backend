"""property_type / typology are closed vocabularies; nothing is guessed beyond them."""
import pytest

from app.core.vocabularies import PROPERTY_TYPES, normalize_property_type, normalize_typology, valid_typologies
from app.services.mapper_service import normalize_generic_payload


@pytest.mark.parametrize("raw,expected", [
    ("Apartamento", "Apartamento"), ("apartamento", "Apartamento"), ("APARTAMENTO", "Apartamento"),
    ("Penthouse", "Apartamento"), ("Duplex", "Apartamento"), ("Cobertura", "Apartamento"), ("Loft", "Apartamento"),
    ("Estúdio", "Apartamento"), ("Apartamento T2 Duplex", "Apartamento"),
    ("Moradia", "Moradia"), ("Moradia Geminada", "Moradia"), ("Vivenda", "Moradia"), ("Villa", "Moradia"),
    ("Quinta", "Quinta"), ("Herdade", "Quinta"), ("Quinta com moradia", "Quinta"),
    ("Terreno Urbano", "Terreno"), ("Lote de terreno", "Terreno"), ("Plot", "Terreno"),
    ("Loja", "Loja"), ("Escritório", "Escritório"), ("escritorio", "Escritório"),
    ("Armazém", "Armazém"), ("Pavilhão", "Armazém"), ("Garagem", "Garagem"), ("Lugar de garagem", "Garagem"),
    ("Prédio", "Prédio"), ("Edifício", "Prédio"), ("Comercial", "Comercial"), ("Restaurante", "Comercial"),
    ("Apartamento com garagem", "Apartamento"),   # earliest keyword wins
    ("Garagem em apartamento", "Garagem"),
    ("Pacote de investimento", "Outro"), ("???", "Outro"),
])
def test_property_type_is_mapped_to_the_closed_set(raw, expected):
    assert normalize_property_type(raw) == expected
    assert expected in PROPERTY_TYPES


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_property_type_stays_none(raw):
    assert normalize_property_type(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("T0", ("T0", None)), ("T2", ("T2", None)), ("t3", ("T3", None)), ("T10", ("T10", None)),
    ("T1+1", ("T1", "+1")), ("T3 + 1", ("T3", "+1")), ("T2+2", ("T2", "+2")),
    ("V3", ("T3", None)), ("V4+1", ("T4", "+1")),
    ("Estúdio", ("T0", None)), ("Studio", ("T0", None)),
    ("Apartamento T2 em Lisboa", ("T2", None)),
    (None, (None, None)), ("", (None, None)), ("Sem tipologia", (None, None)),
    ("T99", (None, None)), ("XT2", (None, None)),
])
def test_typology_split_into_code_and_extra(raw, expected):
    assert normalize_typology(raw) == expected


def test_valid_typologies_are_a_closed_list():
    assert valid_typologies()[:3] == ["T0", "T1", "T2"] and "T20" in valid_typologies()


def test_mapper_applies_both_vocabularies_and_keeps_the_original_in_the_raw_payload():
    raw = {"url": "https://agency.pt/i/1", "title": "Penthouse T1+1 com vista", "property_type": "Penthouse",
           "typology": "T1+1", "price": "250 000 €"}
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert (schema.property_type, schema.typology, schema.typology_extra) == ("Apartamento", "T1", "+1")
    assert schema.raw_partner_payload["property_type"] == "Penthouse"
    assert schema.raw_partner_payload["typology"] == "T1+1"


async def test_backfill_reports_then_normalises_stored_rows(db_session):
    from sqlalchemy import select

    from app.models.listing_model import Listing
    from app.services.listing_cleanup_service import normalize_stored_vocabularies

    db_session.add_all([
        Listing(source_partner="p", source_url="https://x.pt/1", property_type="Penthouse", typology="T1+1"),
        Listing(source_partner="p", source_url="https://x.pt/2", property_type="Apartamento", typology="T2"),
        Listing(source_partner="p", source_url="https://x.pt/3", property_type="Pacote", typology=None),
    ])
    await db_session.commit()

    report = await normalize_stored_vocabularies(db_session, apply=False)
    assert report["changed"] == 2 and report["unrecognised"] == {"Pacote": 1}
    assert (await db_session.execute(select(Listing.property_type).where(Listing.source_url == "https://x.pt/1"))).scalar_one() == "Penthouse"

    await normalize_stored_vocabularies(db_session, apply=True)
    db_session.expire_all()
    row = (await db_session.execute(select(Listing).where(Listing.source_url == "https://x.pt/1"))).scalar_one()
    assert (row.property_type, row.typology, row.typology_extra) == ("Apartamento", "T1", "+1")
    again = await normalize_stored_vocabularies(db_session, apply=False)
    assert again["changed"] == 0                      # idempotent


async def test_vocabularies_endpoint(client):
    body = (await client.get("/api/v1/listings/vocabularies")).json()["data"]
    assert "Apartamento" in body["property_type"] and body["typology"][0] == "T0"
