"""Resolve free-text district / county / parish to official codes (DICOFRE).

The official parish code DICOFRE is six digits, ``DD CC FF``: the first two are
the district, the first four the municipality (DICO) and all six the parish. So a
single table of parishes carries all three levels.

Matching is tolerant of case, accents, punctuation and the "União das freguesias
de …" wrapper, and scoped top-down (a parish is only looked up inside its
municipality) so equal parish names in different municipalities never collide.
Text columns are never rewritten here; only codes are produced. If the dataset
file is absent the normaliser resolves nothing and the scrape carries on.
"""
import csv
import difflib
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "freguesias.csv"
_FUZZY_CUTOFF = 0.93  # conservative: a wrong code is worse than none

_UNION_PREFIX_RE = re.compile(
    r"^(?:uniao (?:das|de) freguesias (?:de |da |do |das |dos )?|uf (?:de |das )?|"
    r"freguesia (?:de |da |do |das |dos )?|concelho (?:de |da |do )?|distrito (?:de |da |do )?)"
)


def fold(text: str | None) -> str:
    """Lower-case, strip accents and punctuation, collapse spaces."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", no_accents)).strip()


def _key(text: str | None) -> str:
    folded = fold(text)
    return _UNION_PREFIX_RE.sub("", folded).strip() or folded


@dataclass(frozen=True)
class GeoMatch:
    district_code: str | None = None
    county_code: str | None = None
    parish_code: str | None = None
    district_name: str | None = None
    county_name: str | None = None
    parish_name: str | None = None

    def __bool__(self) -> bool:
        return bool(self.district_code or self.county_code or self.parish_code)


class GeoIndex:
    """In-memory lookup built from rows with dicofre, freguesia, concelho and distrito."""

    def __init__(self, rows: Iterable[Mapping[str, str]]):
        self._districts: dict[str, tuple[str, str]] = {}                   # key -> (code2, name)
        self._counties: dict[str, dict[str, tuple[str, str]]] = {}         # district_code -> key -> (code4, name)
        self._parishes: dict[str, dict[str, tuple[str, str]]] = {}         # county_code -> key -> (code6, name)
        self._counties_global: dict[str, list[tuple[str, str]]] = {}       # key -> [(code4, name)]
        self._district_names: dict[str, str] = {}
        for row in rows:
            code = (row.get("dicofre") or "").strip().zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            d_code, c_code = code[:2], code[:4]
            d_name, c_name, p_name = (row.get("distrito") or "").strip(), (row.get("concelho") or "").strip(), (row.get("freguesia") or "").strip()
            if d_name:
                self._districts.setdefault(_key(d_name), (d_code, d_name))
                self._district_names.setdefault(d_code, d_name)
            if c_name:
                self._counties.setdefault(d_code, {}).setdefault(_key(c_name), (c_code, c_name))
                entries = self._counties_global.setdefault(_key(c_name), [])
                if (c_code, c_name) not in entries:
                    entries.append((c_code, c_name))
            if p_name:
                self._parishes.setdefault(c_code, {}).setdefault(_key(p_name), (code, p_name))
        self.size = sum(len(v) for v in self._parishes.values())
        self._add_union_aliases()

    def _add_union_aliases(self) -> None:
        """Let "Massarelos" resolve to the union "Lordelo do Ouro e Massarelos".

        Component names are added only where they do not already name a parish of
        the municipality, and only if they are unambiguous inside it.
        """
        for county_code, table in self._parishes.items():
            aliases: dict[str, list[tuple[str, str]]] = {}
            for key, entry in list(table.items()):
                parts = [part.strip() for part in re.split(r" e |,", key) if part.strip()]
                if len(parts) < 2:
                    continue
                for part in parts:
                    aliases.setdefault(part, []).append(entry)
            for alias, entries in aliases.items():
                if alias not in table and len({code for code, _ in entries}) == 1:
                    table[alias] = entries[0]

    # ── lookups ──────────────────────────────────────────────────────────

    @staticmethod
    def _find(table: Mapping[str, tuple[str, str]], text: str | None) -> tuple[str, str] | None:
        key = _key(text)
        if not key:
            return None
        if key in table:
            return table[key]
        close = difflib.get_close_matches(key, list(table), n=2, cutoff=_FUZZY_CUTOFF)
        return table[close[0]] if len(close) == 1 else None

    def _find_district(self, text: str | None) -> tuple[str, str] | None:
        return self._find(self._districts, text)

    def _find_county(self, text: str | None, district_code: str | None) -> tuple[str, str] | None:
        if district_code:
            return self._find(self._counties.get(district_code, {}), text)
        # No district to scope by: accept a county name only if it is unique nationwide.
        candidates = self._counties_global.get(_key(text), [])
        return candidates[0] if len(candidates) == 1 else None

    def _find_parish(self, text: str | None, county_code: str | None) -> tuple[str, str] | None:
        if not county_code:
            return None
        return self._find(self._parishes.get(county_code, {}), text)

    def normalize(self, district: str | None, county: str | None, parish: str | None = None) -> GeoMatch:
        """Codes for whatever levels can be resolved consistently (parish needs its county)."""
        d = self._find_district(district)
        c = self._find_county(county, d[0] if d else None)
        if c and not d:
            d_code = c[0][:2]
            d = (d_code, self._district_names.get(d_code, ""))
        if c and d and c[0][:2] != d[0]:
            c = None  # county that does not belong to the stated district: contradictory input
        p = self._find_parish(parish, c[0] if c else None)
        return GeoMatch(
            district_code=d[0] if d else None,
            county_code=c[0] if c else None,
            parish_code=p[0] if p else None,
            district_name=(d[1] or None) if d else None,
            county_name=c[1] if c else None,
            parish_name=p[1] if p else None,
        )


@lru_cache(maxsize=1)
def get_geo_index() -> GeoIndex | None:
    """The shared index, or None when the dataset file is not installed."""
    path = Path(os.environ.get("CAOP_DATA_PATH") or DEFAULT_DATA_PATH)
    if not path.is_file():
        logger.warning("Geo dataset not found at %s — district/county/parish codes will not be resolved", path)
        return None
    with path.open(encoding="utf-8", newline="") as fh:
        index = GeoIndex(csv.DictReader(fh))
    logger.info("Loaded geo index from %s (%d parishes)", path, index.size)
    return index


def normalize_geo(district: str | None, county: str | None, parish: str | None = None) -> GeoMatch:
    """Resolve codes with the shared index; an empty match when unavailable."""
    index = get_geo_index()
    return index.normalize(district, county, parish) if index else GeoMatch()
