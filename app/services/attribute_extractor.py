"""Fill gaps in listing attributes from the description, only on clear evidence.

Used by the shared mapper when a source did not provide the value itself. Every
rule requires an explicit phrase; ambiguous or hedged mentions ("perto de uma
piscina municipal", "possibilidade de ar condicionado") yield nothing, because
a wrong ``has_pool=True`` is worse than ``None``.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from app.core.vocabularies import normalize_property_type

_FLOOR_PROPERTY_TYPES = frozenset({"Apartamento", "Escritório", "Loja", "Comercial", "Garagem"})


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# ── amenity flags ─────────────────────────────────────────────────────────

_FLAG_TERMS: dict[str, re.Pattern[str]] = {
    "garage": re.compile(r"\bgaragem\b|\bgaragens\b|\blugar(?:es)? de (?:estacionamento|garagem)|\bparqueamento\b"),
    "elevator": re.compile(r"\belevador(?:es)?\b|\bascensor(?:es)?\b"),
    "balcony": re.compile(r"\bvarandas?\b|\bsacadas?\b|\bterracos?\b"),
    "air_conditioning": re.compile(r"\bar[ -]condicionado\b|\bclimatizacao\b|\bclimatizado\b"),
    "pool": re.compile(r"\bpiscinas?\b"),
    "garden": re.compile(r"\bjardim\b|\bjardins\b|\bquintal\b"),
}

# Text just before a term that makes the mention unreliable (not "the property has it").
_HEDGE_BEFORE = re.compile(
    r"(?:perto|junto|proximo|proximidade|frente|vista|a poucos metros|distancia|possibilidade|possivel|licenca|projeto|"
    r"pre[- ]?instalacao|preparad[oa]|ponto|condominio|comum|publico|municipal|infantil|zoologico|escola|colegio)\W+(?:\w+\W+){0,3}$"
)
_HEDGE_AFTER = re.compile(r"^\W*(?:municipal|publico|publica|de infancia|zoologico|da escola|do condominio|comum|condominial)")
_NEGATION_BEFORE = re.compile(r"(?:\bsem|\bnao (?:tem|possui|dispoe de|existe)|\bnenhum[a]?)\W+(?:\w+\W+){0,2}$")

_CONTEXT_CHARS = 60


def _flag_from_text(folded: str, term: re.Pattern[str]) -> bool | None:
    """True on a clear positive mention, False on an explicit negation, else None."""
    positive = False
    negative = False
    for m in term.finditer(folded):
        before = folded[max(0, m.start() - _CONTEXT_CHARS):m.start()]
        after = folded[m.end():m.end() + 25]
        if _NEGATION_BEFORE.search(before):
            negative = True
        elif _HEDGE_BEFORE.search(before) or _HEDGE_AFTER.search(after):
            continue
        else:
            positive = True
    if positive:
        return True
    return False if negative else None


# ── construction year ─────────────────────────────────────────────────────

_YEAR_PATTERNS = (
    re.compile(r"\bconstru(?:ido|ida|cao)\s+(?:em|de|no ano de|datad[ao] de)?\s*((?:18|19|20)\d{2})\b"),
    re.compile(r"\bano\s+de\s+construcao\s*[:\-]?\s*((?:18|19|20)\d{2})\b"),
    re.compile(r"\bdata\s+de\s+construcao\s*[:\-]?\s*((?:18|19|20)\d{2})\b"),
    re.compile(r"\bedificio\s+(?:de|datad[ao] de)\s+((?:18|19|20)\d{2})\b"),
    re.compile(r"\bbuilt\s+in\s+((?:18|19|20)\d{2})\b"),
)


def _construction_year(folded: str) -> int | None:
    years = {
        int(m.group(1))
        for pattern in _YEAR_PATTERNS
        for m in pattern.finditer(folded)
        if 1800 <= int(m.group(1)) <= datetime.now().year + 3
    }
    return years.pop() if len(years) == 1 else None  # conflicting years: say nothing


# ── condition ─────────────────────────────────────────────────────────────

_SUBJECT = r"(?:apartamento|moradia|vivenda|imovel|casa|fracao|edificio|predio|loja|escritorio|armazem|andar)"
_CONDITION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Para recuperar", re.compile(
        r"\bpara (?:recuperar|restaurar|reconstruir|remodelar|reabilitar)\b|\ba necessitar de obras\b|"
        r"\bnecessita de obras\b|\bem ruinas\b|\bruina\b|\bpara obras\b")),
    ("Em construção", re.compile(
        r"\bem construcao\b|\bem fase de construcao\b|\bem fase de acabamentos\b|\bobra em curso\b")),
    ("Novo", re.compile(
        rf"\b{_SUBJECT}\s+(?:totalmente\s+|completamente\s+)?novo\b|\b{_SUBJECT}\s+nov[oa]\b|\bnova construcao\b|"
        r"\bconstrucao nova\b|\bobra nova\b|\bnunca habitad[oa]\b|\bpor estrear\b|\ba estrear\b|\bprimeira habitacao\b")),
    ("Renovado", re.compile(
        rf"\b{_SUBJECT}\s+(?:totalmente\s+|completamente\s+|recentemente\s+|integralmente\s+)?(?:renovad|remodelad|reabilitad|recuperad)[oa]\b|"
        r"\b(?:totalmente|completamente|integralmente|recentemente)\s+(?:renovad|remodelad|reabilitad)[oa]\b")),
)


def _condition(folded: str) -> str | None:
    hits = [label for label, pattern in _CONDITION_RULES if pattern.search(folded)]
    return hits[0] if len(hits) == 1 else None  # contradictory evidence: say nothing


# ── floor ─────────────────────────────────────────────────────────────────

_ORDINALS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6,
             "setimo": 7, "oitavo": 8, "nono": 9, "decimo": 10}
_FLOOR_PATTERNS = (
    re.compile(r"\b(\d{1,2})\s*(?:[º°ª]|o\b|\.o\b)\s*(?:andar|piso)\b"),
    re.compile(r"\b(" + "|".join(_ORDINALS) + r")\s+(?:andar|piso)\b"),
    re.compile(r"\b(?:andar|piso)\s+(\d{1,2})\b"),
)
_GROUND_RE = re.compile(r"\bres[- ]do[- ]chao\b|\br/c\b|\brc\b")
_LAST_RE = re.compile(r"\bultimo\s+(?:andar|piso)\b")


def _floor(folded: str) -> str | None:
    found: set[str] = set()
    if _GROUND_RE.search(folded):
        found.add("R/C")
    if _LAST_RE.search(folded):
        found.add("último")
    for pattern in _FLOOR_PATTERNS:
        for m in pattern.finditer(folded):
            token = m.group(1)
            found.add(str(_ORDINALS[token]) if token in _ORDINALS else str(int(token)))
    return found.pop() if len(found) == 1 else None  # several floors mentioned: ambiguous


# ── public API ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExtractedAttributes:
    flags: dict[str, bool] = field(default_factory=dict)  # garage, elevator, balcony, air_conditioning, pool, garden
    construction_year: int | None = None
    condition: str | None = None
    floor: str | None = None


def extract_attributes(text: str | None, property_type: str | None = None) -> ExtractedAttributes:
    """Attributes that the text states unambiguously. Floors are only read for unit-type properties."""
    if not text or not text.strip():
        return ExtractedAttributes()
    folded = _fold(text)
    flags = {}
    for name, term in _FLAG_TERMS.items():
        value = _flag_from_text(folded, term)
        if value is not None:
            flags[name] = value
    return ExtractedAttributes(
        flags=flags,
        construction_year=_construction_year(folded),
        condition=_condition(folded),
        # Accept raw wording ("Penthouse") as well as the canonical value.
        floor=_floor(folded) if normalize_property_type(property_type) in _FLOOR_PROPERTY_TYPES else None,
    )
