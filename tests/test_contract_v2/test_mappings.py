"""Mappings checked with the values really stored in production (sample of 1,556 listings)."""
import pytest

from app.contract_v2.mappings import condition_code, energy_class, property_type_to_contract, typology_to_contract


@pytest.mark.parametrize(("raw", "expected"), [
    ("Usado", "used"), ("Novo", "new"), ("Em construção", "under_construction"), ("Em Construção", "under_construction"),
    ("Em projecto", "under_construction"), ("Renovado", "renovated"), ("Remodelado", "renovated"), ("Recuperado", "renovated"),
    ("Excelente", "used"), ("Habitável", "used"), ("Por recuperar", "to_renovate"), ("Para recuperar", "to_renovate"),
    ("Ruína", "ruin"), ("Não Aplicável", None), ("N/D", None), ("", None), (None, None), ("algo inesperado", None),
])
def test_condition_values_found_in_production(raw, expected):
    assert condition_code(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("A", "A"), ("A+", "A+"), ("B", "B"), ("B-", "B-"), ("C", "C"), ("D", "D"), ("E", "E"), ("F", "F"),
    ("G", "F"),  # the Portuguese scale ends at F
    ("Isento", "exempt"), ("Exempted", "exempt"), ("Em curso", "pending"),
    ("Unavailable", None), ("N/A", None), ("", None), (None, None),
])
def test_energy_values_found_in_production(raw, expected):
    assert energy_class(raw) == expected


@pytest.mark.parametrize(("raw", "title", "code", "subtype"), [
    ("Apartamento", "Apartamento T2", "apartment", None),
    ("Apartamento", "Duplex T3 com terraço", "apartment", "duplex"),
    ("Apartamento", "Cobertura com vista rio", "apartment", "penthouse"),
    ("Moradia", "Moradia T3", "house", None),
    ("Moradia Isolada", None, "house", "detached"),
    ("Moradia Geminada", None, "townhouse", "semi_detached"),
    ("Moradia", "Moradia em banda T4", "townhouse", None),
    ("Moradia", "Villa de luxo em Cascais", "villa", None),
    ("Palacete", None, "house", None),
    ("Quinta", "Quinta com 2 hectares", "farm", None),
    ("Quinta/Herdade", None, "farm", None),
    ("Terreno", "Terreno urbano para construção", "land", "urban_land"),
    ("Terreno Rústico", None, "land", "rustic_land"),
    ("Terreno Misto", None, "land", "rustic_land"),
    ("Lote", None, "land", "urban_land"),
    ("Lote para Construção", None, "land", "urban_land"),
    ("Loja", None, "shop", None), ("Escritório", None, "office", None), ("Armazém", None, "warehouse", None),
    ("Garagem", None, "garage", None), ("Prédio", None, "building", None), ("Comercial", None, "commercial", None),
    ("Restaurante", None, "commercial", None), ("Outro", None, "other", None), (None, None, "other", None),
    ("Comercial", "Hotel com 20 quartos", "hotel", None),
])
def test_property_types_found_in_production(raw, title, code, subtype):
    result = property_type_to_contract(raw, title)
    assert result["code"] == code
    assert result["subtype_code"] == subtype
    assert result["label"]  # never empty: it is shown as is


def test_property_type_keeps_the_original_text_as_label():
    assert property_type_to_contract("Moradia Geminada")["label"] == "Moradia Geminada"
    assert property_type_to_contract(None)["label"] == "Outro"


@pytest.mark.parametrize(("typology", "extra", "expected"), [
    ("T2", None, {"label": "T2", "main": 2, "extra": 0}),
    ("T1", "+1", {"label": "T1+1", "main": 1, "extra": 1}),
    ("T3", "+2", {"label": "T3+2", "main": 3, "extra": 2}),
    ("T0", None, {"label": "T0", "main": 0, "extra": 0}),
    (None, None, None), ("V3", None, None), ("", None, None),
])
def test_typology(typology, extra, expected):
    assert typology_to_contract(typology, extra) == expected
