"""Fill listings.content_hash for rows scraped before the column existed.

Idempotent and safe to interrupt: it never touches ``updated_at`` (Core UPDATE
with an explicit value) and only processes rows whose hash is still NULL.

    python -m scripts.backfill_content_hash [--batch-size 500]
"""
import argparse
import asyncio

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.utils.content_hash import CONTENT_FIELDS, compute_content_hash


async def main(batch_size: int) -> None:
    done = 0
    async with async_session_factory() as db:
        while True:
            batch = (
                await db.execute(select(Listing).where(Listing.content_hash.is_(None)).limit(batch_size))
            ).scalars().all()
            if not batch:
                break
            for listing in batch:
                urls = (
                    await db.execute(
                        select(MediaAsset.url)
                        .where(MediaAsset.listing_id == listing.id)
                        .order_by(MediaAsset.position.asc().nulls_last(), MediaAsset.created_at.asc())
                    )
                ).scalars().all()
                digest = compute_content_hash({f: getattr(listing, f) for f in CONTENT_FIELDS}, urls)
                await db.execute(
                    update(Listing)
                    .where(Listing.id == listing.id)
                    .values(content_hash=digest, updated_at=Listing.updated_at)
                    .execution_options(synchronize_session=False)
                )
            await db.commit()
            db.expunge_all()
            done += len(batch)
            print(f"{done} listings hashed")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=500)
    asyncio.run(main(parser.parse_args().batch_size))
