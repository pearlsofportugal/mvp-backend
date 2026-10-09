"""Clean agency boilerplate and formatting damage out of listing descriptions.

``description`` keeps what the partner wrote; ``description_clean`` is the
version meant to be shown. Cleaning is conservative: it only removes text that
matches a known boilerplate shape, and fixes formatting only where the pattern
is unambiguous (e.g. a price split as "327. 000€").

Partner-specific blocks go in ``PARTNER_BOILERPLATE`` keyed by ``source_partner``;
the generic rules below apply to every partner.
"""
import re

# ── boilerplate ───────────────────────────────────────────────────────────

# Everything from the marker to the end: the agent-only note is always a trailer
# ("Nota para agentes imobiliários: este imóvel está disponível em regime de partilha…").
_TRAILER_MARKERS = (
    re.compile(r"nota\s+para\s+(?:os\s+)?agentes\s+imobili[aá]rios", re.IGNORECASE),
    re.compile(r"note\s+to\s+(?:real\s+estate\s+)?agents", re.IGNORECASE),
)

# Sentences that are agency self-promotion or licence boilerplate. Matched per
# sentence, so the property description around them is untouched.
_BOILERPLATE_SENTENCES = (
    # AMI licence numbers: "AMI 12345", "Licença AMI n.º 1234", "(AMI: 1234)"
    re.compile(r"\bAMI\b\s*(?:n\.?\s*[ºo°]\s*|:|-|–)?\s*\d{3,6}", re.IGNORECASE),
    re.compile(r"licen[cç]a\s+AMI", re.IGNORECASE),
    re.compile(r"mediador[a]?\s+imobili[aá]ri[ao]\s+licenciad[ao]", re.IGNORECASE),
    re.compile(r"\bsomos\s+uma\s+(?:mediadora|ag[eê]ncia|empresa)\b", re.IGNORECASE),
    re.compile(r"\ba\s+nossa\s+(?:equipa|ag[eê]ncia|empresa)\s+(?:est[aá]|encontra-se)\s+(?:dispon[ií]vel|[aà]\s+sua\s+disposi)", re.IGNORECASE),
    re.compile(r"\b(?:visite|consulte)\s+(?:o\s+nosso\s+site|www\.)", re.IGNORECASE),
)

# source_partner -> extra sentence patterns for that partner's own boilerplate.
PARTNER_BOILERPLATE: dict[str, tuple[re.Pattern[str], ...]] = {}

# ── formatting repairs ────────────────────────────────────────────────────

# "327. 000€", "327 . 000 euros", "€ 1.250. 000": thousands separator followed by a stray space,
# only when a currency follows/precedes so "Piso 2. 300 m² de área" is never merged.
_SPLIT_PRICE_AFTER = re.compile(r"(\d{1,3}(?:\.\d{3})*)\s*\.\s+(\d{3})(?!\d)(?=\s*(?:€|eur\b|euros?\b))", re.IGNORECASE)
_SPLIT_PRICE_BEFORE = re.compile(r"(€\s*\d{1,3}(?:\.\d{3})*)\s*\.\s+(\d{3})(?!\d)")
# " . Fachada:" — a bullet that lost its marker (a lone dot between spaces before "Label:").
_LOST_BULLET = re.compile(r"(?:(?<=\s)|^)\.\s+(?=[A-ZÀ-ÝÇ][^:.\n]{1,40}:)")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÝÇ0-9\"“(])")


def _cut_trailer(text: str) -> str:
    cut = len(text)
    for marker in _TRAILER_MARKERS:
        m = marker.search(text)
        if m:
            cut = min(cut, m.start())
    return text[:cut]


def _drop_boilerplate_sentences(text: str, source_partner: str | None) -> str:
    patterns = _BOILERPLATE_SENTENCES + PARTNER_BOILERPLATE.get((source_partner or "").lower(), ())
    kept_lines = []
    for line in text.split("\n"):
        sentences = _SENTENCE_SPLIT.split(line)
        kept = [s for s in sentences if not any(p.search(s) for p in patterns)]
        kept_lines.append(" ".join(kept))
    return "\n".join(kept_lines)


def _fix_formatting(text: str) -> str:
    text = _SPLIT_PRICE_AFTER.sub(r"\1.\2", text)
    text = _SPLIT_PRICE_BEFORE.sub(r"\1.\2", text)
    text = _LOST_BULLET.sub("\n- ", text)
    return text


def _tidy(text: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue  # no leading or doubled blank lines
        out.append(line)
    return "\n".join(out).strip()


def clean_description(text: str | None, source_partner: str | None = None) -> str | None:
    """Return the display-ready description, or None when nothing meaningful is left."""
    if not text or not text.strip():
        return None
    cleaned = _cut_trailer(text)
    cleaned = _fix_formatting(cleaned)
    cleaned = _drop_boilerplate_sentences(cleaned, source_partner)
    cleaned = _tidy(cleaned)
    return cleaned or None


# ── quality score ─────────────────────────────────────────────────────────

_FACT_RE = re.compile(r"\d+\s*(?:m2|m²|metros|quartos|casas de banho|wc|suites?|su[ií]tes?)|\bT\d\b|\b(?:19|20)\d{2}\b", re.IGNORECASE)


def score_description(clean: str | None) -> int | None:
    """0-100 heuristic for how useful a (cleaned) description is. None when there is no text.

    Deterministic and free: length (up to 50), structure — several sentences or
    paragraphs — (up to 20), concrete facts such as areas/rooms/years (up to 20),
    and not shouting in capitals (up to 10). It ranks descriptions; it does not judge prose.
    """
    if not clean:
        return None
    length = len(clean)
    score = min(50, round(length / 600 * 50))
    sentences = len([s for s in _SENTENCE_SPLIT.split(clean.replace("\n", " ")) if len(s.strip()) > 20])
    score += min(20, sentences * 4)
    score += min(20, len(_FACT_RE.findall(clean)) * 5)
    letters = [c for c in clean if c.isalpha()]
    upper_ratio = sum(c.isupper() for c in letters) / len(letters) if letters else 0
    score += 10 if upper_ratio < 0.3 else 0
    return max(0, min(100, score))
