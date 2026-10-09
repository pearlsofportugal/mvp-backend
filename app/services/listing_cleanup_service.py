"""One-off clean-up of shell records stored before junk-page detection existed.

Pages that returned HTTP 404/410 (rendered by Playwright) were persisted as
listings titled "410" with almost every field null. They are archived — flagged
``status='removed'`` — never deleted, and the affected rows are exported to CSV
first so the change can be audited or reverted.
"""
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lifecycle import STATUS_ACTIVE, STATUS_REMOVED
from app.models.listing_model import Listing

_NUMERIC_TITLE_RE = re.compile(r"^\s*\d{1,5}\s*$")
_BACKUP_COLUMNS = ("id", "source_partner", "source_url", "title", "status", "created_at", "updated_at")


async def find_junk_listings(db: AsyncSession) -> list[Listing]:
    """Active rows with a numeric/empty title, no property type and no price."""
    rows = (
        await db.execute(
            select(Listing).where(
                Listing.status == STATUS_ACTIVE,
                Listing.property_type.is_(None),
                Listing.price_amount.is_(None),
                Listing.price_on_request.isnot(True),
            )
        )
    ).scalars().all()
    return [r for r in rows if not (r.title or "").strip() or _NUMERIC_TITLE_RE.match(r.title)]


def write_backup(listings: list[Listing], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_BACKUP_COLUMNS)
        for item in listings:
            writer.writerow([getattr(item, col) for col in _BACKUP_COLUMNS])


async def archive_junk_listings(db: AsyncSession, backup_path: Path, *, apply: bool) -> list[Listing]:
    """Return the affected rows; when ``apply`` is set, back them up then flag them removed."""
    junk = await find_junk_listings(db)
    if not apply or not junk:
        return junk
    write_backup(junk, backup_path)
    await db.execute(
        update(Listing)
        .where(Listing.id.in_([r.id for r in junk]), Listing.status == STATUS_ACTIVE)
        .values(status=STATUS_REMOVED, removed_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return junk


# ── vocabulary backfill ───────────────────────────────────────────────────

async def normalize_stored_vocabularies(db: AsyncSession, *, apply: bool) -> dict:
    """Re-normalise property_type / typology of stored rows to the closed vocabularies.

    Returns counts plus the raw values that fell into "Outro", so gaps in the
    vocabulary are visible. With ``apply`` the rows are updated through the ORM,
    which bumps ``updated_at`` — the content really did change for consumers.
    """
    from collections import Counter

    from app.core.vocabularies import PROPERTY_TYPE_OTHER, normalize_property_type, normalize_typology

    changed = 0
    unrecognised: Counter[str] = Counter()
    rows = (await db.execute(select(Listing))).scalars().all()
    for row in rows:
        new_type = normalize_property_type(row.property_type)
        new_typology, new_extra = normalize_typology(row.typology)
        if new_extra is None and new_typology == row.typology:
            new_extra = row.typology_extra  # already split on a previous run: keep the "+1"
        if new_type == PROPERTY_TYPE_OTHER and row.property_type:
            unrecognised[row.property_type] += 1
        if (new_type, new_typology, new_extra) == (row.property_type, row.typology, row.typology_extra):
            continue
        changed += 1
        if apply:
            row.property_type, row.typology, row.typology_extra = new_type, new_typology, new_extra
    if apply and changed:
        await db.commit()
    return {"scanned": len(rows), "changed": changed, "unrecognised": dict(unrecognised.most_common())}
