"""Helpers for working out image URLs and (when the source states them) sizes."""
import base64
import re

# WordPress and many CMSes name generated renditions "photo-1024x768.jpg": the
# suffix is the rendition's real size. Requested-size URL params (eGO "/Z1280x960/",
# "?w=800") are only a bounding box, so they are deliberately not parsed.
_SUFFIX_SIZE_RE = re.compile(r"-(\d{2,5})x(\d{2,5})\.(?:jpe?g|png|webp|gif|avif)(?:\?|$)", re.IGNORECASE)
_MIN_SIDE = 50


def dimensions_from_url(url: str) -> tuple[int, int] | None:
    m = _SUFFIX_SIZE_RE.search(url or "")
    if not m:
        return None
    width, height = int(m.group(1)), int(m.group(2))
    return (width, height) if width >= _MIN_SIDE and height >= _MIN_SIDE else None


def parse_srcset(srcset: str | None) -> list[tuple[str, int | None]]:
    """[(url, width_px or None)] for a srcset; ``2x``-style descriptors give no width."""
    candidates: list[tuple[str, int | None]] = []
    for part in (srcset or "").split(","):
        tokens = part.strip().split()
        if not tokens or tokens[0].startswith("data:"):
            continue
        width = None
        if len(tokens) > 1 and tokens[1].lower().endswith("w") and tokens[1][:-1].isdigit():
            width = int(tokens[1][:-1])
        candidates.append((tokens[0], width))
    return candidates


def largest_srcset_candidate(srcset: str | None) -> tuple[str, int | None] | None:
    """The widest candidate (or the last one when no widths are declared)."""
    candidates = parse_srcset(srcset)
    if not candidates:
        return None
    with_width = [c for c in candidates if c[1]]
    return max(with_width, key=lambda c: c[1]) if with_width else candidates[-1]


_XIMO_OPTIMIZER_RE = re.compile(r"^https?://[^/]*ximocrm\.com/image-optimizer/[^/]+/([A-Za-z0-9_-]{16,})(?:\.[a-z0-9]+)?(?:\?.*)?$", re.IGNORECASE)


def ximo_original_url(url: str) -> str:
    """Original image URL behind a ximocrm ``image-optimizer`` link.

    The optimizer serves a resized WebP and carries the source address as
    unpadded urlsafe base64 in the last path segment; the source is the
    full-size JPEG on the agency's media host. Anything that is not such a
    link (or does not decode to an http(s) URL) is returned unchanged.
    """
    m = _XIMO_OPTIMIZER_RE.match(url or "")
    if not m:
        return url
    token = m.group(1)
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url
    return decoded if decoded.startswith(("http://", "https://")) else url
