"""Closed vocabularies for `property_type` and `typology`.

Partners describe the same thing in dozens of ways ("Penthouse", "Duplex T3",
"Vivenda", "T1+1"). Normalising to a fixed set lets consumers (the WordPress
plugin, filters, exports) map each value once. Values stay Portuguese because
that is what the columns already hold, so the common cases ("Apartamento",
"Moradia", "Terreno") are unchanged for existing clients.
"""
import re
import unicodedata

PROPERTY_TYPES: tuple[str, ...] = (
    "Apartamento",
    "Moradia",
    "Quinta",
    "Terreno",
    "Loja",
    "Escritório",
    "Armazém",
    "Garagem",
    "Prédio",
    "Comercial",
    "Outro",
)
PROPERTY_TYPE_OTHER = "Outro"

# canonical type -> accent-free keyword regex (matched against the folded input).
_PROPERTY_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, re.compile(pattern))
    for canonical, pattern in (
        ("Apartamento", r"\bapartamento|\bapartment|\bflat\b|\bpenthouse|\bcobertura|\bduplex|\btriplex|\bloft\b|\bestudio|\bstudio|\bandar\b"),
        ("Moradia", r"\bmoradia|\bvivenda|\bvilla\b|\bhouse\b|\bcasa\b|\bchalet|\bbungalow|\bmansion|\btownhouse|\bcottage|\bfarmhouse|\bdetached|\bsolar\b|\bpalacete|\bpalacio"),
        ("Quinta", r"\bquinta|\bquintinha|\bherdade|\bmonte\b|\bfarm|\bhomestead|\bturismo rural|\bturismo em espaco rural"),
        ("Terreno", r"\bterreno|\blote\b|\bland\b|\blot\b|\bparcela|\bgleba|\bground\b|\bplot\b"),
        ("Loja", r"\bloja|\bstore\b|\bshop\b"),
        ("Escritório", r"\bescritorio|\boffice|\bgabinete"),
        ("Armazém", r"\barmazem|\bwarehouse|\bpavilhao|\bindustrial|\bfabrica|\boficina"),
        ("Garagem", r"\bgaragem|\bgarage|\bparking|\bestacionamento"),
        ("Prédio", r"\bpredio|\bedificio|\bbuilding|\bblock of flats"),
        ("Comercial", r"\bcomercial|\bcommercial|\brestaurante|\bcafe\b|\bbar\b|\bpastelaria|\bhotel|\bhostel|\balojamento|\bestabelecimento|\bespaco\b|\bclinica|\bfarmacia|\bpousada|\bbusiness\b|\btrespasse"),
    )
)


def fold(text: str) -> str:
    """Lower-case and strip accents so 'Escritório'/'escritorio'/'ESCRITÓRIO' compare equal."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_property_type(raw: str | None) -> str | None:
    """Map free text onto PROPERTY_TYPES. None/blank -> None; unrecognised -> 'Outro'.

    The earliest keyword in the text wins ("Apartamento com garagem" is an
    Apartamento, not a Garagem); ties go to the order of PROPERTY_TYPES.
    """
    if raw is None or not str(raw).strip():
        return None
    folded = fold(str(raw))
    best: tuple[int, int, str] | None = None
    for priority, (canonical, pattern) in enumerate(_PROPERTY_TYPE_PATTERNS):
        match = pattern.search(folded)
        if match and (best is None or (match.start(), priority) < best[:2]):
            best = (match.start(), priority, canonical)
    return best[2] if best else PROPERTY_TYPE_OTHER


# ── typology ──────────────────────────────────────────────────────────────

MAX_BEDROOMS_IN_TYPOLOGY = 20
_TYPOLOGY_RE = re.compile(r"(?<![a-z0-9])[tv]\s*(\d{1,2})(?:\s*\+\s*(\d{1,2}))?(?![a-z0-9])", re.IGNORECASE)
_STUDIO_RE = re.compile(r"\b(?:estudio|studio|kitchenette)\b")


def valid_typologies() -> list[str]:
    return [f"T{n}" for n in range(0, MAX_BEDROOMS_IN_TYPOLOGY + 1)]


def normalize_typology(raw: str | None) -> tuple[str | None, str | None]:
    """('T1+1' | 'v3' | 'Estúdio') -> (typology, extra): ('T1', '+1') | ('T3', None) | ('T0', None).

    Moradias use the V prefix (V3) for the same bedroom count, so V maps to T to
    keep the vocabulary closed. Anything that is not a recognisable code gives
    (None, None) rather than a guess.
    """
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw)
    match = _TYPOLOGY_RE.search(text)
    if match:
        bedrooms = int(match.group(1))
        if bedrooms > MAX_BEDROOMS_IN_TYPOLOGY:
            return None, None
        extra = f"+{int(match.group(2))}" if match.group(2) else None
        return f"T{bedrooms}", extra
    if _STUDIO_RE.search(fold(text)):
        return "T0", None
    return None, None
