"""District / county / parish -> official codes, tolerant of accents, case and wrappers."""
import pytest

from app.models.listing_model import Listing
from app.services import geo_normalizer
from app.services.geo_normalizer import GeoIndex, GeoMatch, fold, normalize_geo

# A miniature of the real table (dicofre, freguesia, concelho, distrito).
ROWS = [
    {"dicofre": "131208", "freguesia": "União das freguesias de Lordelo do Ouro e Massarelos", "concelho": "Porto", "distrito": "Porto"},
    {"dicofre": "131201", "freguesia": "Aldoar, Foz do Douro e Nevogilde", "concelho": "Porto", "distrito": "Porto"},
    {"dicofre": "131211", "freguesia": "Paranhos", "concelho": "Porto", "distrito": "Porto"},
    {"dicofre": "110633", "freguesia": "Santa Maria Maior", "concelho": "Lisboa", "distrito": "Lisboa"},
    {"dicofre": "110618", "freguesia": "Estrela", "concelho": "Lisboa", "distrito": "Lisboa"},
    {"dicofre": "060307", "freguesia": "Santa Maria Maior", "concelho": "Coimbra", "distrito": "Coimbra"},
    {"dicofre": "150805", "freguesia": "Quarteira", "concelho": "Loulé", "distrito": "Faro"},
    {"dicofre": "030312", "freguesia": "Paranhos", "concelho": "Braga", "distrito": "Braga"},
]


@pytest.fixture
def index() -> GeoIndex:
    return GeoIndex(ROWS)


def test_codes_are_derived_from_the_parish_code(index):
    m = index.normalize("Porto", "Porto", "Paranhos")
    assert (m.district_code, m.county_code, m.parish_code) == ("13", "1312", "131211")
    assert (m.district_name, m.county_name, m.parish_name) == ("Porto", "Porto", "Paranhos")


@pytest.mark.parametrize("district,county,parish", [
    ("PORTO", "porto", "PARANHOS"), ("  Porto ", "Porto", "Paranhos"), ("Porto", "Porto", "paranhós"),
    ("Distrito do Porto", "Concelho do Porto", "Freguesia de Paranhos"),
])
def test_tolerates_case_accents_and_wrappers(index, district, county, parish):
    assert index.normalize(district, county, parish).parish_code == "131211"


def test_accents_in_municipality_names(index):
    assert index.normalize("Faro", "Loule", "Quarteira").parish_code == "150805"
    assert index.normalize("FARO", "LOULÉ", None).county_code == "1508"


def test_union_of_parishes_matches_with_or_without_the_wrapper_and_by_component(index):
    for text in ("União das freguesias de Lordelo do Ouro e Massarelos", "Lordelo do Ouro e Massarelos",
                 "UF Lordelo do Ouro e Massarelos", "Massarelos", "Lordelo do Ouro"):
        assert index.normalize("Porto", "Porto", text).parish_code == "131208", text


def test_same_parish_name_in_different_municipalities_never_collides(index):
    assert index.normalize("Lisboa", "Lisboa", "Santa Maria Maior").parish_code == "110633"
    assert index.normalize("Coimbra", "Coimbra", "Santa Maria Maior").parish_code == "060307"
    assert index.normalize("Porto", "Porto", "Paranhos").parish_code == "131211"
    assert index.normalize("Braga", "Braga", "Paranhos").parish_code == "030312"


def test_parish_needs_its_municipality(index):
    m = index.normalize("Porto", None, "Paranhos")
    assert (m.district_code, m.county_code, m.parish_code) == ("13", None, None)


def test_county_alone_infers_the_district_when_unique(index):
    m = index.normalize(None, "Loulé", None)
    assert (m.district_code, m.county_code) == ("15", "1508")


def test_county_outside_the_stated_district_is_rejected(index):
    m = index.normalize("Porto", "Lisboa", "Estrela")
    assert (m.county_code, m.parish_code) == (None, None)


def test_unknown_or_blank_input_resolves_nothing(index):
    assert not index.normalize("Narnia", "Cair Paravel", "Anvard")
    assert not index.normalize(None, None, None)
    assert index.normalize("Porto", "Porto", "Bairro Inventado").parish_code is None


def test_small_typo_is_tolerated_but_not_a_different_name(index):
    assert index.normalize("Porto", "Porto", "Paranhoss").parish_code == "131211"
    assert index.normalize("Lisboa", "Lisboa", "Estrelas").parish_code == "110618"
    assert index.normalize("Lisboa", "Lisboa", "Santa Maria").parish_code is None


def test_fold():
    assert fold("  União das Freguesias, de São João! ") == "uniao das freguesias de sao joao"


def test_missing_dataset_degrades_to_no_codes(monkeypatch, tmp_path):
    monkeypatch.setenv("CAOP_DATA_PATH", str(tmp_path / "nope.csv"))
    geo_normalizer.get_geo_index.cache_clear()
    try:
        assert normalize_geo("Porto", "Porto", "Paranhos") == GeoMatch()
    finally:
        geo_normalizer.get_geo_index.cache_clear()


def test_dataset_loaded_from_csv(monkeypatch, tmp_path):
    path = tmp_path / "freguesias.csv"
    path.write_text("dicofre,freguesia,concelho,distrito\n131211,Paranhos,Porto,Porto\n", encoding="utf-8")
    monkeypatch.setenv("CAOP_DATA_PATH", str(path))
    geo_normalizer.get_geo_index.cache_clear()
    try:
        assert normalize_geo("porto", "porto", "paranhos").parish_code == "131211"
    finally:
        geo_normalizer.get_geo_index.cache_clear()


@pytest.fixture
def with_index(monkeypatch):
    index = GeoIndex(ROWS)
    monkeypatch.setattr(geo_normalizer, "get_geo_index", lambda: index)


def test_mapper_adds_codes_and_keeps_text_as_scraped(with_index):
    from app.services.mapper_service import normalize_generic_payload

    raw = {"url": "https://agency.pt/i/1", "title": "Apartamento T2", "property_type": "Apartamento", "price": "300 000 €",
           "district": "PORTO", "county": "Porto", "parish": "Paranhos"}
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert (schema.district_code, schema.county_code, schema.parish_code) == ("13", "1312", "131211")
    assert (schema.address.region, schema.address.city, schema.address.area) == ("PORTO", "Porto", "Paranhos")


async def test_filters_and_api_expose_codes(client, db_session):
    db_session.add_all([
        Listing(source_partner="p", source_url="https://x.pt/1", title="a", district_code="13", county_code="1312", parish_code="131211"),
        Listing(source_partner="p", source_url="https://x.pt/2", title="b", district_code="11", county_code="1106", parish_code="110618"),
    ])
    await db_session.commit()
    items = (await client.get("/api/v1/listings", params={"county_code": "1312"})).json()["data"]["items"]
    assert [i["source_url"] for i in items] == ["https://x.pt/1"]
    by_district = (await client.get("/api/v1/listings", params={"district_code": "11"})).json()["data"]["items"]
    assert [i["source_url"] for i in by_district] == ["https://x.pt/2"]
    assert (await client.get("/api/v1/listings", params={"parish_code": "12"})).status_code == 422
    detail = (await client.get(f"/api/v1/listings/{by_district[0]['id']}")).json()["data"]
    assert (detail["district_code"], detail["county_code"], detail["parish_code"]) == ("11", "1106", "110618")


async def test_backfill_fills_codes_and_reports_unmatched(db_session, monkeypatch):
    from sqlalchemy import select

    from app.services.listing_cleanup_service import backfill_geo_codes

    index = GeoIndex(ROWS)
    monkeypatch.setattr(geo_normalizer, "get_geo_index", lambda: index)
    db_session.add_all([
        Listing(source_partner="p", source_url="https://x.pt/1", title="a", district="Porto", county="Porto", parish="Paranhós"),
        Listing(source_partner="p", source_url="https://x.pt/2", title="b", district="Narnia", county="Cair Paravel"),
    ])
    await db_session.commit()

    dry = await backfill_geo_codes(db_session, apply=False)
    assert dry["changed"] == 1 and dry["unmatched"] == {"Narnia / Cair Paravel": 1}
    await backfill_geo_codes(db_session, apply=True)
    db_session.expire_all()
    row = (await db_session.execute(select(Listing).where(Listing.source_url == "https://x.pt/1"))).scalar_one()
    assert (row.parish_code, row.parish) == ("131211", "Paranhós")     # text untouched
