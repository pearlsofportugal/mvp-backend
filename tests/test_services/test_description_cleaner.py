"""description_clean: boilerplate out, formatting repaired, original untouched."""
import pytest

from app.services import description_cleaner
from app.services.description_cleaner import clean_description, score_description
from app.services.mapper_service import normalize_generic_payload


def test_agent_note_trailer_is_cut():
    text = ("Apartamento T2 renovado, com varanda.\n\n"
            "Nota para agentes imobiliários: este imóvel está disponível para partilha de comissão. Contacte o consultor.")
    assert clean_description(text) == "Apartamento T2 renovado, com varanda."


def test_agent_note_without_accent_and_flattened():
    text = "Moradia com jardim. Nota para agentes imobiliarios: partilha 50%."
    assert clean_description(text) == "Moradia com jardim."


def test_ami_licence_sentence_is_dropped_but_description_kept():
    text = "Excelente apartamento junto ao metro. Mediadora imobiliária licenciada AMI 12345. Cozinha equipada."
    assert clean_description(text) == "Excelente apartamento junto ao metro. Cozinha equipada."


@pytest.mark.parametrize("text", ["Licença AMI n.º 1234", "AMI: 98765", "(AMI 4321)"])
def test_ami_variants(text):
    assert clean_description(f"Bela vista de rio. {text}. Piso 3.") == "Bela vista de rio. Piso 3."


def test_agency_presentation_sentence_is_dropped():
    text = "T3 com garagem. Somos uma agência com 20 anos de experiência no mercado. Perto de escolas."
    assert clean_description(text) == "T3 com garagem. Perto de escolas."


@pytest.mark.parametrize("raw,fixed", [
    ("Preço: 327. 000€", "Preço: 327.000€"),
    ("Por apenas 1.250. 000 euros", "Por apenas 1.250.000 euros"),
    ("Valor € 450. 000", "Valor € 450.000"),
    ("Por 327 . 000€", "Por 327.000€"),
])
def test_split_prices_are_repaired(raw, fixed):
    assert clean_description(raw) == fixed


def test_ordinary_numbers_are_not_merged():
    text = "Fica no piso 2. 300 m² de área total."
    assert clean_description(text) == text


def test_lost_bullets_become_a_list():
    text = "Características: . Fachada: pedra . Pavimento: madeira . Cozinha: equipada"
    assert clean_description(text) == "Características:\n- Fachada: pedra\n- Pavimento: madeira\n- Cozinha: equipada"


def test_normal_sentences_with_dots_are_untouched():
    text = "Moradia isolada. Tem 3 quartos. Garagem para 2 carros."
    assert clean_description(text) == text


def test_whitespace_is_tidied_and_empty_results_are_none():
    assert clean_description("  Linha um.  \n\n\n  Linha   dois. ") == "Linha um.\n\nLinha dois."
    assert clean_description("Nota para agentes imobiliários: tudo aqui.") is None
    assert clean_description(None) is None and clean_description("   ") is None


def test_partner_specific_rules(monkeypatch):
    import re
    monkeypatch.setitem(description_cleaner.PARTNER_BOILERPLATE, "acme", (re.compile(r"acme homes garante", re.IGNORECASE),))
    text = "Casa nova. Acme Homes garante o melhor negócio. Vista mar."
    assert clean_description(text, "acme") == "Casa nova. Vista mar."
    assert clean_description(text, "other") == text


def test_quality_score_ranks_rich_over_poor():
    poor = score_description("Casa.")
    rich = score_description(
        "Moradia T4 com 220 m² de área bruta, construída em 2005. Possui 3 casas de banho e garagem fechada. "
        "O jardim com 400 m² tem piscina e zona de churrasco. Localizada perto de escolas e comércio. "
        "Cozinha totalmente equipada com ilha e despensa. Aquecimento central e painéis solares."
    )
    assert 0 <= poor < rich <= 100
    assert score_description(None) is None
    assert score_description("TUDO EM MAIÚSCULAS PARA CHAMAR A ATENÇÃO DO LEITOR") < score_description(
        "Tudo em minúsculas para chamar a atenção do leitor com calma.")


def test_mapper_keeps_description_intact_and_adds_clean_copy():
    raw = {"url": "https://agency.pt/i/1", "title": "Apartamento T2", "property_type": "Apartamento",
           "price": "327.000 €",
           "raw_description": "Ótimo T2. Preço 327. 000€. Nota para agentes imobiliários: partilha."}
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert "Nota para agentes" in schema.descriptions["raw"]
    assert "Nota para agentes" in schema.descriptions["pt"]          # original, untouched
    assert schema.descriptions["clean"] == "Ótimo T2. Preço 327.000€."
    assert schema.description_quality_score is not None
