"""Fill description_clean and description_quality_score for stored listings.

Dry-run by default. `description` is never modified. With --apply the rows are
updated through the ORM, which bumps updated_at (the API output changed).

    python -m scripts.backfill_description_clean
    python -m scripts.backfill_description_clean --apply
"""
import argparse
import asyncio

from app.database import async_session_factory
from app.services.listing_cleanup_service import backfill_description_clean


async def main(apply: bool) -> None:
    async with async_session_factory() as db:
        report = await backfill_description_clean(db, apply=apply)
    print(f"scanned {report['scanned']}, {'updated' if apply else 'would update'} {report['changed']}")
    if not apply:
        print("Dry run — nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
