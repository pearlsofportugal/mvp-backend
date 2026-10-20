"""The parish dataset must ship with the app (it was missing, so every geo code was null)."""
from app.services.geo_normalizer import DEFAULT_DATA_PATH, get_geo_index, normalize_geo


def test_dataset_file_is_in_the_repo():
    assert DEFAULT_DATA_PATH.is_file()
    assert DEFAULT_DATA_PATH.stat().st_size > 100_000


def test_shipped_dataset_resolves_real_locations():
    assert get_geo_index().size > 3000
    m = normalize_geo("Porto", "Vila Nova de Gaia", "Canidelo")
    assert (m.district_code, m.county_code, m.parish_code) == ("13", "1317", "131704")
    # Madeira is an island "district" in DICOFRE; the partner text says "Madeira".
    assert normalize_geo("Madeira", "Funchal", "Santo António").county_code == "3103"


def test_unknown_parish_stays_empty_rather_than_guessing():
    m = normalize_geo("Porto", "Matosinhos", "Freguesia Inexistente")
    assert m.county_code == "1308" and m.parish_code is None
