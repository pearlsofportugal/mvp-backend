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


# ── description backfill ──────────────────────────────────────────────────

async def backfill_description_clean(db: AsyncSession, *, apply: bool) -> dict:
    """Fill description_clean / description_quality_score for stored rows from their raw text."""
    from app.services.description_cleaner import clean_description, score_description

    changed = 0
    rows = (await db.execute(select(Listing))).scalars().all()
    for row in rows:
        clean = clean_description(row.raw_description or row.description, row.source_partner)
        score = score_description(clean)
        if (clean, score) == (row.description_clean, row.description_quality_score):
            continue
        changed += 1
        if apply:
            row.description_clean, row.description_quality_score = clean, score
    if apply and changed:
        await db.commit()
    return {"scanned": len(rows), "changed": changed}


# ── geo codes backfill ────────────────────────────────────────────────────

async def backfill_geo_codes(db: AsyncSession, *, apply: bool) -> dict:
    """Resolve district/county/parish codes for stored rows. Text columns are never changed."""
    from collections import Counter

    from app.services import geo_normalizer

    index = geo_normalizer.get_geo_index()
    if index is None:
        return {"scanned": 0, "changed": 0, "unmatched": {}, "dataset": False}

    changed = 0
    unmatched: Counter[str] = Counter()
    rows = (await db.execute(select(Listing))).scalars().all()
    for row in rows:
        if not (row.district or row.county or row.parish):
            continue
        match = index.normalize(row.district, row.county, row.parish)
        new = (match.district_code, match.county_code, match.parish_code)
        if row.county and not match.county_code:
            unmatched[f"{row.district} / {row.county}"] += 1
        if new == (row.district_code, row.county_code, row.parish_code) or not any(new):
            continue
        changed += 1
        if apply:
            row.district_code, row.county_code, row.parish_code = new
    if apply and changed:
        await db.commit()
    return {"scanned": len(rows), "changed": changed, "unmatched": dict(unmatched.most_common(50)), "dataset": True}


# ── attribute backfill ────────────────────────────────────────────────────

_FLAG_COLUMNS = {
    "garage": "has_garage", "elevator": "has_elevator", "balcony": "has_balcony",
    "air_conditioning": "has_air_conditioning", "pool": "has_pool", "garden": "has_garden",
}


async def backfill_attributes(db: AsyncSession, *, apply: bool) -> dict:
    """Fill has_*, construction_year, condition and floor that are still NULL, from the text.

    Only NULL columns are touched, only on explicit evidence (see attribute_extractor).
    """
    from app.services.attribute_extractor import extract_attributes

    filled = {"rows": 0, "fields": 0}
    rows = (await db.execute(select(Listing))).scalars().all()
    for row in rows:
        found = extract_attributes(f"{row.title or ''} . {row.raw_description or row.description or ''}", row.property_type)
        updates = {_FLAG_COLUMNS[name]: value for name, value in found.flags.items()}
        updates.update(construction_year=found.construction_year, condition=found.condition, floor=found.floor)
        updates = {col: v for col, v in updates.items() if v is not None and getattr(row, col) is None}
        if not updates:
            continue
        filled["rows"] += 1
        filled["fields"] += len(updates)
        if apply:
            for col, value in updates.items():
                setattr(row, col, value)
    if apply and filled["rows"]:
        await db.commit()
    return {"scanned": len(rows), **filled}
