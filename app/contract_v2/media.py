"""`media[]`: stable URLs, a thumbnail where the host has a known thumbnail rule, cover flag, watermark flag."""
import re

MAX_MEDIA = 60

_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "avif": "image/avif"}
_EXT = re.compile(r"\.([a-z0-9]{3,4})(?:\?.*)?$", re.IGNORECASE)
_CENTRALIMO = re.compile(r"^(https://imgs\.centralimo\.pt/.+/properties/[0-9a-f-]+)\.(jpe?g|png|webp)$", re.IGNORECASE)
_EGO_SIZE = re.compile(r"/Z\d+x\d+/")
_TYPES = {"photo", "floorplan", "video", "virtual_tour", "document"}


def thumb_url(url: str) -> str | None:
    """Thumbnail of a photo for hosts with a documented thumbnail pattern, else None."""
    if m := _CENTRALIMO.match(url):
        return f"{m.group(1)}_s.{m.group(2)}"
    if "images.egorealestate.com" in url and _EGO_SIZE.search(url):
        return _EGO_SIZE.sub("/Z320x240/", url, count=1)
    return None


def mime_type(url: str) -> str | None:
    m = _EXT.search(url)
    return _MIME.get(m.group(1).lower()) if m else None


def serialize_media(assets) -> list[dict]:
    """Ordered, at most MAX_MEDIA items; the first photo is the cover."""
    ordered = sorted(assets or [], key=lambda a: (a.position if a.position is not None else 10**6))
    items: list[dict] = []
    cover_set = False
    for asset in ordered[:MAX_MEDIA]:
        url = str(asset.url)
        kind = asset.type if asset.type in _TYPES else "photo"
        is_cover = kind == "photo" and not cover_set
        cover_set = cover_set or is_cover
        items.append({
            "id": str(asset.id),
            "type": kind,
            "url": url,
            "thumb_url": thumb_url(url) if kind == "photo" else None,
            "width": asset.width,
            "height": asset.height,
            "bytes": None,
            "mime": mime_type(url),
            "position": len(items),
            "is_cover": is_cover,
            "caption": None,
            "alt": {"pt": asset.alt_text} if getattr(asset, "alt_text", None) else None,
            "has_watermark": True if "/watermark/" in url else None,
            "hotlink_ok": None,
            "checksum": None,
        })
    return items
