"""Fill district_code / county_code / parish_code for stored listings.

Needs the parish dataset (app/data/freguesias.csv, or CAOP_DATA_PATH). Dry-run by
default; the text columns district/county/parish are never changed. With --apply the
rows are updated through the ORM, which bumps updated_at (new fields in the API).
Prints the most frequent district / county pairs that could not be matched.

    python -m scripts.backfill_geo_codes
    python -m scripts.backfill_geo_codes --apply
"""
import argparse
import asyncio

from app.database import async_session_factory
from app.services.listing_cleanup_service import backfill_geo_codes


async def main(apply: bool) -> None:
    async with async_session_factory() as db:
        report = await backfill_geo_codes(db, apply=apply)
    if not report["dataset"]:
        print("Geo dataset not found (app/data/freguesias.csv). Nothing to do.")
        return
    print(f"scanned {report['scanned']}, {'updated' if apply else 'would update'} {report['changed']}")
    if report["unmatched"]:
        print("county not matched (district / county -> count):")
        for pair, count in report["unmatched"].items():
            print(f"  {count:>5}  {pair}")
    if not apply:
        print("\nDry run - nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
