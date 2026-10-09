"""Fill NULL has_*, construction_year, condition and floor from the listing text.

Dry-run by default. Only NULL columns are filled, only from explicit phrases.
With --apply the rows are updated through the ORM, which bumps updated_at.

    python -m scripts.backfill_attributes
    python -m scripts.backfill_attributes --apply
"""
import argparse
import asyncio

from app.database import async_session_factory
from app.services.listing_cleanup_service import backfill_attributes


async def main(apply: bool) -> None:
    async with async_session_factory() as db:
        report = await backfill_attributes(db, apply=apply)
    print(f"scanned {report['scanned']}; {report['fields']} field(s) in {report['rows']} listing(s) "
          f"{'filled' if apply else 'would be filled'}")
    if not apply:
        print("Dry run — nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
