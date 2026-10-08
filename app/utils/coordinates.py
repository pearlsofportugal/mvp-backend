"""Extract a property's coordinates from its source page.

Sources are tried from most to least trustworthy: structured data (JSON-LD,
meta tags, microdata), explicit ``data-*`` attributes, Google Maps links and
embeds, and finally inline scripts. A wrong pin on a map is worse than none, so
a tier that yields several *different* points (a results map, a "similar
listings" widget) is treated as ambiguous and skipped.

No geocoding happens here — if the page carries no coordinates the result is
``None`` and the listing simply has no location_precision.
"""
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import unquote

from bs4 import BeautifulSoup

# (lat_min, lat_max, lng_min, lng_max): mainland, Madeira, Azores.
_PT_BOXES = (
    (36.8, 42.2, -9.6, -6.1),
    (32.3, 33.2, -17.4, -16.1),
    (36.8, 39.9, -31.5, -24.8),
)
_NUM = r"(-?\d{1,3}(?:\.\d+)?)"
_MAPS_AT_RE = re.compile(rf"@{_NUM},\s*{_NUM}")
_MAPS_QUERY_RE = re.compile(rf"[?&;](?:q|ll|center|query|destination|daddr)=\s*{_NUM}\s*(?:,|%2C)\s*{_NUM}", re.I)
_MAPS_EMBED_RE = re.compile(rf"!3d{_NUM}!4d{_NUM}")
_SCRIPT_LATLNG_CALL_RE = re.compile(rf"LatLng\(\s*{_NUM}\s*,\s*{_NUM}\s*\)", re.I)
_SCRIPT_KEYED_RE = re.compile(
    rf"""["']?\blat(?:itude)?["']?\s*[:=]\s*["']?{_NUM}["']?\s*,\s*["']?\b(?:lng|lon|long|longitude)["']?\s*[:=]\s*["']?{_NUM}""",
    re.I,
)
_MAP_HOST_RE = re.compile(r"google\.[a-z.]+/maps|maps\.google|goo\.gl/maps|openstreetmap\.org|bing\.com/maps|waze\.com", re.I)


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float
    source: str  # jsonld | meta | data-attr | maps-link | script


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace(",", ".")) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_plausible_portugal(lat: float | None, lng: float | None) -> bool:
    """True when the point falls inside Portugal (mainland, Madeira or Azores) and is not 0,0."""
    if lat is None or lng is None or (lat == 0 and lng == 0):
        return False
    return any(a <= lat <= b and c <= lng <= d for a, b, c, d in _PT_BOXES)


def _valid_pair(lat: Any, lng: Any) -> tuple[float, float] | None:
    la, lo = _to_float(lat), _to_float(lng)
    return (la, lo) if is_plausible_portugal(la, lo) else None


def _pick(candidates: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """The single distinct point among the candidates, or None if absent/ambiguous."""
    distinct = {(round(la, 5), round(lo, 5)): (la, lo) for la, lo in candidates}
    return next(iter(distinct.values())) if len(distinct) == 1 else None


# ── tiers ──────────────────────────────────────────────────────────────────

# The agency's own office is not the property: skip those nodes (and anything
# nested in them) so a RealEstateAgent `geo` never becomes the listing's pin.
_AGENCY_TYPES = frozenset({
    "organization", "localbusiness", "realestateagent", "store", "corporation",
    "person", "professionalservice", "homeandconstructionbusiness",
})


def _is_agency(node: dict) -> bool:
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(isinstance(t, str) and t.lower() in _AGENCY_TYPES for t in types)


def _walk_json(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        if _is_agency(node):
            return
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def _from_jsonld(soup: BeautifulSoup) -> list[tuple[float, float]]:
    found = []
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        for node in _walk_json(data):
            pair = _valid_pair(node.get("latitude"), node.get("longitude"))
            if pair:
                found.append(pair)
    return found


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").lower()
        if key in wanted and tag.get("content"):
            return tag["content"]
    return None


def _from_meta(soup: BeautifulSoup) -> list[tuple[float, float]]:
    found = []
    pair = _valid_pair(
        _meta_content(soup, "place:location:latitude", "og:latitude", "latitude"),
        _meta_content(soup, "place:location:longitude", "og:longitude", "longitude"),
    )
    if pair:
        found.append(pair)
    for raw in (_meta_content(soup, "geo.position"), _meta_content(soup, "ICBM")):
        if raw:
            parts = re.split(r"[;,]", raw)
            if len(parts) == 2 and (pair := _valid_pair(*parts)):
                found.append(pair)
    # microdata: <span itemprop="latitude" content="..">
    lat_el, lng_el = soup.find(attrs={"itemprop": "latitude"}), soup.find(attrs={"itemprop": "longitude"})
    if lat_el and lng_el:
        pair = _valid_pair(lat_el.get("content") or lat_el.get_text(), lng_el.get("content") or lng_el.get_text())
        if pair:
            found.append(pair)
    return found


def _from_data_attrs(soup: BeautifulSoup) -> list[tuple[float, float]]:
    found = []
    for el in soup.find_all(True):
        attrs = el.attrs
        lat = attrs.get("data-lat") or attrs.get("data-latitude")
        lng = attrs.get("data-lng") or attrs.get("data-lon") or attrs.get("data-long") or attrs.get("data-longitude")
        if lat is not None and lng is not None and (pair := _valid_pair(lat, lng)):
            found.append(pair)
            continue
        combined = attrs.get("data-latlng") or attrs.get("data-coordinates") or attrs.get("data-position")
        if isinstance(combined, str):
            parts = re.split(r"[;,\s]+", combined.strip("[](){} "))
            if len(parts) == 2 and (pair := _valid_pair(*parts)):
                found.append(pair)
    return found


def _from_map_links(soup: BeautifulSoup) -> list[tuple[float, float]]:
    found = []
    for el in soup.find_all(["a", "iframe"]):
        target = unquote(el.get("href") or el.get("src") or el.get("data-src") or "")
        if not _MAP_HOST_RE.search(target):
            continue
        for regex in (_MAPS_AT_RE, _MAPS_QUERY_RE, _MAPS_EMBED_RE):
            for lat, lng in regex.findall(target):
                if pair := _valid_pair(lat, lng):
                    found.append(pair)
    return found


def _from_scripts(soup: BeautifulSoup) -> list[tuple[float, float]]:
    found = []
    for script in soup.find_all("script"):
        if "ld+json" in (script.get("type") or "").lower():
            continue
        text = script.string or script.get_text() or ""
        if len(text) > 400_000:
            continue
        for regex in (_SCRIPT_LATLNG_CALL_RE, _SCRIPT_KEYED_RE):
            for lat, lng in regex.findall(text):
                if pair := _valid_pair(lat, lng):
                    found.append(pair)
    return found


_TIERS = (
    ("jsonld", _from_jsonld),
    ("meta", _from_meta),
    ("data-attr", _from_data_attrs),
    ("maps-link", _from_map_links),
    ("script", _from_scripts),
)


def extract_coordinates(page: "str | BeautifulSoup") -> Coordinates | None:
    """Best coordinates found on a listing page, or None."""
    soup = page if isinstance(page, BeautifulSoup) else BeautifulSoup(page or "", "lxml")
    for source, finder in _TIERS:
        pair = _pick(finder(soup))
        if pair:
            return Coordinates(latitude=pair[0], longitude=pair[1], source=source)
    return None
