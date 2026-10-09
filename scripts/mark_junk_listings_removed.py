"""Archive "410"-style shell listings by flagging them status='removed'.

Dry-run by default. Nothing is ever deleted; with --apply the affected rows are
first written to a CSV backup, then flagged removed (reversible by setting
status back to 'active', ids are in the CSV).

    python -m scripts.mark_junk_listings_removed            # report only
    python -m scripts.mark_junk_listings_removed --apply    # backup + flag
"""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.database import async_session_factory
from app.services.listing_cleanup_service import archive_junk_listings


async def main(apply: bool, backup_dir: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"junk_listings_{stamp}.csv"
    async with async_session_factory() as db:
        junk = await archive_junk_listings(db, backup_path, apply=apply)

    print(f"{len(junk)} shell listing(s) found")
    for item in junk[:20]:
        print(f"  {item.id}  {item.source_partner:<20} title={item.title!r}  {item.source_url}")
    if len(junk) > 20:
        print(f"  ... and {len(junk) - 20} more")
    if not junk:
        return
    if apply:
        print(f"\nFlagged as removed. Backup: {backup_path}")
    else:
        print("\nDry run — nothing changed. Re-run with --apply to back up and flag them.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the backup CSV and flag rows removed")
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.backup_dir))
