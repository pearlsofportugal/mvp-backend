"""Coordinates are taken from the source page only; ambiguous or foreign points are dropped."""
import pytest

from app.services.mapper_service import normalize_generic_payload, parse_coordinates
from app.services.parser_service import parse_listing_page
from app.utils.coordinates import extract_coordinates, is_plausible_portugal

PORTO = (41.1579, -8.6291)


def _page(head: str = "", body: str = "") -> str:
    return f"<html><head>{head}</head><body><h1>Apartamento T2</h1>{body}</body></html>"


def test_jsonld_geo():
    html = _page(
        '<script type="application/ld+json">{"@type":"Apartment","name":"x",'
        '"geo":{"@type":"GeoCoordinates","latitude":41.1579,"longitude":"-8.6291"}}</script>'
    )
    c = extract_coordinates(html)
    assert (c.latitude, c.longitude, c.source) == (*PORTO, "jsonld")


def test_jsonld_agency_office_is_not_the_property():
    html = _page(
        '<script type="application/ld+json">{"@type":"RealEstateAgent","geo":'
        '{"latitude":38.7223,"longitude":-9.1393}}</script>'
    )
    assert extract_coordinates(html) is None


def test_jsonld_listing_with_nested_agency_uses_the_listing_point():
    html = _page(
        '<script type="application/ld+json">{"@graph":[{"@type":"RealEstateListing","geo":'
        '{"latitude":41.1579,"longitude":-8.6291}},{"@type":"Organization","geo":'
        '{"latitude":38.7223,"longitude":-9.1393}}]}</script>'
    )
    c = extract_coordinates(html)
    assert (c.latitude, c.longitude) == PORTO


@pytest.mark.parametrize("head", [
    '<meta property="place:location:latitude" content="41.1579"><meta property="place:location:longitude" content="-8.6291">',
    '<meta name="geo.position" content="41.1579;-8.6291">',
    '<meta name="ICBM" content="41.1579, -8.6291">',
])
def test_meta_tags(head):
    c = extract_coordinates(_page(head))
    assert (c.latitude, c.longitude, c.source) == (*PORTO, "meta")


def test_data_attributes_and_microdata():
    c = extract_coordinates(_page(body='<div id="map" data-lat="41.1579" data-lng="-8.6291"></div>'))
    assert (c.latitude, c.longitude, c.source) == (*PORTO, "data-attr")
    c = extract_coordinates(_page(body='<div id="map" data-latlng="41.1579,-8.6291"></div>'))
    assert (c.latitude, c.longitude) == PORTO


@pytest.mark.parametrize("url", [
    "https://www.google.com/maps/@41.1579,-8.6291,17z",
    "https://www.google.com/maps?q=41.1579,-8.6291",
    "https://www.google.com/maps/embed?pb=!1m18!2m1!3d41.1579!4d-8.6291!2m3",
    "https://maps.google.com/maps?ll=41.1579%2C-8.6291&z=15",
])
def test_google_maps_links_and_embeds(url):
    for tag in (f'<a href="{url}">mapa</a>', f'<iframe src="{url}"></iframe>'):
        c = extract_coordinates(_page(body=tag))
        assert (c.latitude, c.longitude, c.source) == (*PORTO, "maps-link"), tag


@pytest.mark.parametrize("script", [
    "var m = new google.maps.LatLng(41.1579, -8.6291);",
    "var cfg = {lat: 41.1579, lng: -8.6291, zoom: 15};",
    'var cfg = {"latitude": "41.1579", "longitude": "-8.6291"};',
])
def test_inline_scripts(script):
    c = extract_coordinates(_page(body=f"<script>{script}</script>"))
    assert (c.latitude, c.longitude, c.source) == (*PORTO, "script")


def test_ambiguous_points_are_not_guessed():
    """A results/similar-listings map exposes many points: better no pin than a wrong one."""
    body = '<div data-lat="41.15" data-lng="-8.62"></div><div data-lat="38.72" data-lng="-9.13"></div>'
    assert extract_coordinates(_page(body=body)) is None


def test_same_point_repeated_is_not_ambiguous():
    body = '<div data-lat="41.1579" data-lng="-8.6291"></div><a href="https://www.google.com/maps?q=41.1579,-8.6291">x</a>'
    assert extract_coordinates(_page(body=body)).latitude == 41.1579


@pytest.mark.parametrize("lat,lng", [(0, 0), (40.7128, -74.006), (51.5, -0.12), (None, -8.6), (41.1, None)])
def test_out_of_portugal_or_missing_is_rejected(lat, lng):
    assert not is_plausible_portugal(lat, lng)
    assert parse_coordinates(lat, lng) == (None, None)


def test_islands_are_accepted():
    assert is_plausible_portugal(32.65, -16.91)   # Funchal
    assert is_plausible_portugal(37.74, -25.67)   # Ponta Delgada


def test_page_without_coordinates():
    assert extract_coordinates(_page(body="<p>Sem mapa</p>")) is None


def test_parse_listing_page_to_schema_end_to_end():
    html = _page(
        '<meta property="place:location:latitude" content="41.1579">'
        '<meta property="place:location:longitude" content="-8.6291">',
        '<span class="price">250 000 €</span>',
    )
    raw = parse_listing_page(html, "https://agency.pt/i/1", {})
    assert (raw["latitude"], raw["longitude"]) == PORTO
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert (schema.latitude, schema.longitude, schema.location_precision) == (*PORTO, "exact")


def test_schema_without_coordinates_has_no_precision():
    raw = parse_listing_page(_page(), "https://agency.pt/i/2", {})
    schema = normalize_generic_payload(raw, "generic_agency_pt")
    assert schema.latitude is None and schema.location_precision is None
