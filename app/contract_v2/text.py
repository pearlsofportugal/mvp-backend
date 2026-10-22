"""Text rules of the contract: titles 5-120 chars, descriptions >= 40, no HTML, no contacts."""
import html
import re

from app.core.vocabularies import fold

TITLE_MIN, TITLE_MAX = 5, 120
DESCRIPTION_MIN = 40
META_MAX = 170

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Portuguese mobile (91/92/93/96) and landline (2xx) numbers, with optional +351/00351 and separators
_PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)351[\s.-]?)?(?:9[1236]\d|2\d\d)[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)")


def clean_text(value: str | None) -> str:
    """Plain text: tags and entities removed, spaces and blank lines collapsed."""
    if not value:
        return ""
    text = html.unescape(_TAG.sub(" ", str(value)))
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", text).strip()


def has_contact(text: str | None) -> bool:
    return bool(text and (_EMAIL.search(text) or _PHONE.search(text)))


def strip_contacts(text: str) -> str:
    """Remove phone numbers and e-mail addresses (agency contacts do not belong in the listing text)."""
    cleaned = _PHONE.sub("", _EMAIL.sub("", text))
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def slugify(value: str | None) -> str | None:
    if not value:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", fold(value)).strip("-")
    return slug or None


def _cut(text: str, limit: int) -> str:
    """Shorten to *limit* characters on a word boundary, ending with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut or text[: limit - 1]) + "…"


def make_title(raw: str | None, fallback: str) -> str:
    """5-120 characters: the source title (shortened on a word boundary) or *fallback* when it is too short."""
    title = clean_text(raw).replace("\n", " ")
    title = strip_contacts(title)
    if len(title) < TITLE_MIN:
        title = fallback
    return _cut(title, TITLE_MAX)


def make_description(raw: str | None, fallback: str) -> tuple[str, bool]:
    """(text, was_synthesised). >= 40 characters, no contacts; *fallback* stands in for an empty/short text."""
    text = strip_contacts(clean_text(raw))
    if len(text) >= DESCRIPTION_MIN:
        return text, False
    synthesised = fallback if len(fallback) >= DESCRIPTION_MIN else f"{fallback} Consulte o anúncio na origem para mais informação."
    return synthesised, True


def make_meta_description(raw: str | None) -> str | None:
    text = clean_text(raw).replace("\n", " ")
    return _cut(text, META_MAX) if text else None
