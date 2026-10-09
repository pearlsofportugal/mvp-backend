"""Normalise stored property_type / typology to the closed vocabularies.

Dry-run by default. With --apply the rows are updated (which bumps updated_at,
because the values consumers see change; the WordPress sync will re-import them).
The unrecognised property types that become "Outro" are printed so the
vocabulary in app/core/vocabularies.py can be extended before applying.

    python -m scripts.backfill_vocabularies            # report only
    python -m scripts.backfill_vocabularies --apply
"""
import argparse
import asyncio

from app.database import async_session_factory
from app.services.listing_cleanup_service import normalize_stored_vocabularies


async def main(apply: bool) -> None:
    async with async_session_factory() as db:
        report = await normalize_stored_vocabularies(db, apply=apply)
    print(f"scanned {report['scanned']}, {'updated' if apply else 'would update'} {report['changed']}")
    if report["unrecognised"]:
        print("property_type values mapped to 'Outro':")
        for value, count in report["unrecognised"].items():
            print(f"  {count:>5}  {value!r}")
    if not apply:
        print("\nDry run — nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
