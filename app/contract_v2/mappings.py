"""Stable codes of the v2 contract and the rules that map our stored text onto them.

Everything here is a pure function of the stored value, so each rule is testable with the
real values found in production (see tests/test_contract_v2/test_mappings.py).
"""
import re

from app.core.vocabularies import fold, normalize_property_type

# ── property type ───────────────────────────────────────────────────────────────────────────────────

# canonical Portuguese vocabulary (app/core/vocabularies.py) -> contract code
PROPERTY_TYPE_CODES: dict[str, str] = {
    "Apartamento": "apartment",
    "Moradia": "house",
    "Quinta": "farm",
    "Terreno": "land",
    "Loja": "shop",
    "Escritório": "office",
    "Armazém": "warehouse",
    "Garagem": "garage",
    "Prédio": "building",
    "Comercial": "commercial",
    "Outro": "other",
}

PROPERTY_TYPE_LABELS: dict[str, tuple[str, str]] = {  # code -> (pt, en)
    "apartment": ("Apartamento", "Apartment"),
    "house": ("Moradia", "House"),
    "villa": ("Villa", "Villa"),
    "townhouse": ("Moradia em banda", "Townhouse"),
    "land": ("Terreno", "Land"),
    "farm": ("Quinta", "Farm"),
    "commercial": ("Comercial", "Commercial"),
    "office": ("Escritório", "Office"),
    "shop": ("Loja", "Shop"),
    "warehouse": ("Armazém", "Warehouse"),
    "garage": ("Garagem", "Garage"),
    "building": ("Prédio", "Building"),
    "hotel": ("Hotel", "Hotel"),
    "other": ("Outro", "Other"),
}

SUBTYPE_LABELS: dict[str, tuple[str, str]] = {
    "detached": ("Isolada", "Detached"),
    "semi_detached": ("Geminada", "Semi-detached"),
    "duplex": ("Duplex", "Duplex"),
    "penthouse": ("Cobertura", "Penthouse"),
    "studio": ("Estúdio", "Studio"),
    "loft": ("Loft", "Loft"),
    "urban_land": ("Terreno urbano", "Urban land"),
    "rustic_land": ("Terreno rústico", "Rustic land"),
}

_VILLA = re.compile(r"\bvilla\b")
_TOWNHOUSE = re.compile(r"\bem banda\b|\bgeminada\b|\btownhouse\b|\bbanda\b")
_HOTEL = re.compile(r"\bhotel\b|\bhostel\b|\bpousada\b|\balojamento local\b")
_SUBTYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("semi_detached", re.compile(r"\bgeminada\b|\bsemi[- ]?detached\b")),
    ("detached", re.compile(r"\bisolada\b|\bdetached\b")),
    ("penthouse", re.compile(r"\bpenthouse\b|\bcobertura\b")),
    ("duplex", re.compile(r"\bduplex\b|\btriplex\b")),
    ("studio", re.compile(r"\bestudio\b|\bstudio\b")),
    ("loft", re.compile(r"\bloft\b")),
    ("rustic_land", re.compile(r"\brustic[oa]\b|\bagricola\b|\bmisto\b|\bflorestal\b")),
    ("urban_land", re.compile(r"\burban[oa]\b|\bpara construcao\b|\blote\b|\bconstrucao\b")),
)


def property_type_to_contract(raw: str | None, title: str | None = None) -> dict:
    """{code, label, subtype_code, subtype_label} from the stored text (and the title for the subtype).

    The stored value is not always normalised ("Moradia Isolada", "Lote", "Quinta/Herdade"), so it is
    always run through the shared vocabulary first.
    """
    canonical = normalize_property_type(raw) or "Outro"
    code = PROPERTY_TYPE_CODES.get(canonical, "other")
    text = fold(f"{raw or ''} {title or ''}")

    if code == "house":
        if _VILLA.search(text):
            code = "villa"
        elif _TOWNHOUSE.search(text):
            code = "townhouse"
    if code in {"commercial", "building", "other"} and _HOTEL.search(text):
        code = "hotel"

    subtype = next((name for name, pattern in _SUBTYPES if pattern.search(text)), None)
    # land subtypes only make sense for land; building subtypes only for dwellings
    if subtype in {"urban_land", "rustic_land"} and code != "land":
        subtype = None
    if subtype in {"detached", "semi_detached"} and code not in {"house", "villa", "townhouse"}:
        subtype = None
    if subtype in {"penthouse", "duplex", "studio", "loft"} and code not in {"apartment", "house"}:
        subtype = None

    label = (raw or "").strip() or PROPERTY_TYPE_LABELS[code][0]
    return {
        "code": code,
        "label": label,
        "subtype_code": subtype,
        "subtype_label": SUBTYPE_LABELS[subtype][0] if subtype else None,
    }


# ── condition ───────────────────────────────────────────────────────────────────────────────────────

CONDITION_LABELS: dict[str, tuple[str, str]] = {
    "new": ("Novo", "New"),
    "under_construction": ("Em construção", "Under construction"),
    "used": ("Usado", "Used"),
    "renovated": ("Renovado", "Renovated"),
    "to_renovate": ("Para recuperar", "To renovate"),
    "ruin": ("Ruína", "Ruin"),
}

# Order matters: "por recuperar" must be tested before "recuperado".
_CONDITION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ruin", re.compile(r"\bruina")),
    ("to_renovate", re.compile(r"(?:por|para|a) (?:recuperar|renovar|remodelar|reabilitar)|necessita (?:de )?(?:obras|recuperac)|a necessitar")),
    ("under_construction", re.compile(r"em construcao|em projecto|em projeto|em planta|em obras|construcao")),
    ("renovated", re.compile(r"renovad|remodelad|recuperad|restaurad|reabilitad")),
    ("new", re.compile(r"^novo$|^nova$|^new$|a estrear|\bnovo\b|\bnova\b")),
    ("used", re.compile(r"usad|em uso|habitavel|excelente|bom estado|muito bom|^bom$|mobilado")),
)


def condition_code(raw: str | None) -> str | None:
    """'Usado' | 'Em Construção' | 'Por recuperar' | 'Não Aplicável' ... -> contract code or None."""
    if raw is None or not str(raw).strip():
        return None
    text = fold(str(raw))
    for code, pattern in _CONDITION_RULES:
        if pattern.search(text):
            return code
    return None


# ── energy ──────────────────────────────────────────────────────────────────────────────────────────

ENERGY_CLASSES = ("A+", "A", "B", "B-", "C", "D", "E", "F", "exempt", "pending")
ENERGY_LABELS: dict[str, tuple[str, str]] = {
    **{c: (c, c) for c in ("A+", "A", "B", "B-", "C", "D", "E", "F")},
    "exempt": ("Isento", "Exempt"),
    "pending": ("Em curso", "Pending"),
}
_ENERGY_LETTERS = {"a+": "A+", "a": "A", "b": "B", "b-": "B-", "c": "C", "d": "D", "e": "E", "f": "F", "g": "F"}
_ENERGY_EXEMPT = re.compile(r"isent|exempt")
_ENERGY_PENDING = re.compile(r"em curso|pending|em processo|em tramitacao")


def energy_class(raw: str | None) -> str | None:
    """'A+' | 'Isento' | 'Exempted' | 'Unavailable' | 'G' ... -> contract value or None.

    The Portuguese scale ends at F; a stray 'G' (some agencies copy the European scale) is the
    worst class we have, so it maps to 'F'. Unavailable / not applicable is unknown, not a class.
    """
    if raw is None or not str(raw).strip():
        return None
    text = fold(str(raw)).replace(" ", "")
    if text in _ENERGY_LETTERS:
        return _ENERGY_LETTERS[text]
    spaced = fold(str(raw))
    if _ENERGY_EXEMPT.search(spaced):
        return "exempt"
    if _ENERGY_PENDING.search(spaced):
        return "pending"
    return None


# ── typology ────────────────────────────────────────────────────────────────────────────────────────

_TYPOLOGY = re.compile(r"^T(\d{1,2})$", re.IGNORECASE)


def typology_to_contract(typology: str | None, extra: str | None) -> dict | None:
    """('T1', '+1') -> {label: 'T1+1', main: 1, extra: 1}; unknown -> None."""
    if not typology:
        return None
    match = _TYPOLOGY.match(typology.strip())
    if not match:
        return None
    main = int(match.group(1))
    extra_n = 0
    if extra:
        digits = re.sub(r"\D", "", extra)
        extra_n = int(digits) if digits else 0
    label = f"T{main}" + (f"+{extra_n}" if extra_n else "")
    return {"label": label, "main": main, "extra": extra_n}


# ── fixed enumerations exposed by /taxonomies ───────────────────────────────────────────────────────

BUSINESS_TYPES: dict[str, tuple[str, str]] = {
    "sale": ("Venda", "Sale"),
    "rent": ("Arrendamento", "Rent"),
    "trespasse": ("Trespasse", "Business transfer"),
}
STATUSES = ("active", "reserved", "sold", "rented", "removed")
