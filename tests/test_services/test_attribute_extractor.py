"""Attributes are inferred only from explicit phrases; the source's own value wins."""
import pytest

from app.services.attribute_extractor import extract_attributes
from app.services.mapper_service import normalize_generic_payload


def _flags(text):
    return extract_attributes(text).flags


@pytest.mark.parametrize("text,name", [
    ("Apartamento com garagem para dois carros.", "garage"),
    ("Prédio com elevador.", "elevator"),
    ("Sala com acesso a varanda.", "balcony"),
    ("Cozinha e sala com ar condicionado.", "air_conditioning"),
    ("Moradia com piscina privada.", "pool"),
    ("Moradia com jardim e quintal.", "garden"),
])
def test_clear_mentions_set_the_flag(text, name):
    assert _flags(text) == {name: True}


@pytest.mark.parametrize("text,name", [
    ("Sem garagem.", "garage"), ("Prédio sem elevador.", "elevator"), ("Não tem piscina.", "pool"),
    ("Sem ar condicionado.", "air_conditioning"),
])
def test_explicit_negation_sets_false(text, name):
    assert _flags(text) == {name: False}


@pytest.mark.parametrize("text", [
    "Perto de uma piscina municipal.", "Junto ao jardim público.", "Pré-instalação de ar condicionado.",
    "Possibilidade de construir garagem.", "A poucos metros do jardim de infância.", "Licença para piscina.",
    "Acesso ao elevador do condomínio comum.", "Cozinha com box de duche.",
])
def test_hedged_or_unrelated_mentions_are_ignored(text):
    assert _flags(text) == {}


def test_positive_beats_nothing_but_negative_is_not_forced_by_a_hedge():
    assert _flags("Sem garagem. Perto de garagem pública.") == {"garage": False}


@pytest.mark.parametrize("text,year", [
    ("Edifício construído em 1998.", 1998), ("Ano de construção: 2005", 2005), ("Construção de 1987, remodelada.", 1987),
    ("Moradia construida no ano de 1975.", 1975), ("Construído em 1998 e ampliado em 2008.", 1998),
])
def test_construction_year(text, year):
    assert extract_attributes(text).construction_year == year


@pytest.mark.parametrize("text", [
    "Renovado em 2019.", "Livro de 1850.", "Inaugurado em 2001.",
    "Construção prevista para 2030.",
])
def test_construction_year_needs_unambiguous_construction_phrase(text):
    assert extract_attributes(text).construction_year is None


@pytest.mark.parametrize("text,condition", [
    ("Moradia para recuperar.", "Para recuperar"), ("Imóvel a necessitar de obras.", "Para recuperar"),
    ("Apartamento novo, nunca habitado.", "Novo"), ("Nova construção com acabamentos de luxo.", "Novo"),
    ("Apartamento totalmente renovado.", "Renovado"), ("Moradia recentemente remodelada.", "Renovado"),
    ("Empreendimento em construção.", "Em construção"),
])
def test_condition_from_clear_phrases(text, condition):
    assert extract_attributes(text).condition == condition


@pytest.mark.parametrize("text", [
    "Cozinha nova e casa de banho renovada.", "Bom estado de conservação.", "Apartamento novo para recuperar.",
])
def test_condition_ignores_partial_or_contradictory_evidence(text):
    assert extract_attributes(text).condition is None


@pytest.mark.parametrize("text,floor", [
    ("Apartamento no 3º andar.", "3"), ("Localizado no segundo andar com vista.", "2"), ("Rés-do-chão com logradouro.", "R/C"),
    ("Fração no último andar.", "último"), ("Piso 4 com elevador.", "4"),
])
def test_floor_for_units(text, floor):
    assert extract_attributes(text, "Apartamento").floor == floor


def test_floor_is_not_read_for_houses_or_when_ambiguous():
    assert extract_attributes("Moradia com 2º andar e r/c.", "Moradia").floor is None
    assert extract_attributes("Do 2º andar ao 3º andar.", "Apartamento").floor is None
    assert extract_attributes("Prédio com 4 andares.", "Apartamento").floor is None


def test_empty_text():
    assert extract_attributes(None).flags == {} and extract_attributes("  ").condition is None


def test_mapper_fills_gaps_but_never_overrides_the_source():
    raw = {"url": "https://agency.pt/i/1", "title": "Apartamento T2", "property_type": "Apartamento", "price": "200 000 €",
           "garage": "No", "raw_description": "T2 no 3º andar, com garagem e piscina. Construído em 1990. Totalmente renovado."}
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert schema.features.has_garage is False            # the source said no: kept
    assert schema.features.has_pool is True               # inferred
    assert schema.features.has_elevator is None           # no evidence: not invented
    assert (schema.floor, schema.construction_year) == ("3", 1990)
    assert schema.condition == "Renovado"


async def test_backfill_only_fills_null_columns(db_session):
    from sqlalchemy import select

    from app.models.listing_model import Listing
    from app.services.listing_cleanup_service import backfill_attributes

    db_session.add(Listing(
        source_partner="p", source_url="https://x.pt/1", title="T2", property_type="Apartamento",
        raw_description="No 2º andar, com elevador e piscina. Construído em 1999.",
        has_pool=False, construction_year=None))
    await db_session.commit()

    assert (await backfill_attributes(db_session, apply=False))["fields"] == 3   # dry run: nothing written
    await backfill_attributes(db_session, apply=True)
    db_session.expire_all()
    row = (await db_session.execute(select(Listing))).scalar_one()
    assert (row.has_elevator, row.construction_year, row.floor) == (True, 1999, "2")
    assert row.has_pool is False                       # stored value kept even though the text says "piscina"


def test_floor_accepts_raw_property_type_wording():
    assert extract_attributes("Penthouse no 5º andar.", "Penthouse").floor == "5"
    assert extract_attributes("Moradia no 5º andar.", "Vivenda").floor is None
