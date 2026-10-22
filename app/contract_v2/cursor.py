"""Opaque keyset cursor over (updated_at, id): stable for incremental sync (`?updated_since=` + `?cursor=`)."""
import base64
import json
from datetime import datetime, timezone
from uuid import UUID


class InvalidCursor(ValueError):
    pass


def encode_cursor(updated_at: datetime, listing_id: UUID) -> str:
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    payload = json.dumps({"u": updated_at.astimezone(timezone.utc).isoformat(), "i": str(listing_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        return datetime.fromisoformat(data["u"]), UUID(data["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidCursor("Invalid cursor") from exc
