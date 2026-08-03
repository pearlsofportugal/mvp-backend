from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing
from app.services.imodigi_service import get_listing_ids_for_bulk_imodigi
from tests.conftest import make_listing_payload


async def test_get_listing_ids_for_bulk_imodigi_filters_source_partner_and_enriched(db_session):
    now = datetime.now(timezone.utc)

    listing_published_and_enriched = Listing(
        **make_listing_payload(
            source_url="https://example.com/1",
            source_partner="pearls",
            enriched_translations={"pt": {"title": "Enriched"}},
            created_at=now,
            updated_at=now,
        )
    )
    listing_other_partner = Listing(
        **make_listing_payload(
            source_url="https://example.com/2",
            source_partner="other",
            enriched_translations=None,
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
    )
    listing_pearls_not_enriched = Listing(
        **make_listing_payload(
            source_url="https://example.com/3",
            source_partner="pearls",
            enriched_translations=None,
            created_at=now + timedelta(seconds=2),
            updated_at=now + timedelta(seconds=2),
        )
    )

    db_session.add_all([
        listing_published_and_enriched,
        listing_other_partner,
        listing_pearls_not_enriched,
    ])
    await db_session.commit()

    db_session.add(
        ImodigiExport(
            listing_id=listing_published_and_enriched.id,
            status="published",
        )
    )
    await db_session.commit()

    result_ids = await get_listing_ids_for_bulk_imodigi(
        db_session,
        listing_ids=[],
        limit=10,
        source_partner="pearls",
        is_enriched=False,
    )

    assert result_ids == [listing_pearls_not_enriched.id]

    result_ids_enriched = await get_listing_ids_for_bulk_imodigi(
        db_session,
        listing_ids=[],
        limit=10,
        source_partner="pearls",
        is_enriched=True,
    )

    assert result_ids_enriched == []


async def test_get_listing_ids_for_bulk_imodigi_backs_off_recently_failed_listings(db_session):
    """A listing that failed export within the cooldown window must not keep
    occupying the sync budget on every run — it should yield to a listing
    that has never been attempted, even though the failed one is older.
    """
    now = datetime.now(timezone.utc)

    recently_failed = Listing(
        **make_listing_payload(
            source_url="https://example.com/failed-recent",
            created_at=now,
            updated_at=now,
        )
    )
    long_failed = Listing(
        **make_listing_payload(
            source_url="https://example.com/failed-long-ago",
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
    )
    never_tried = Listing(
        **make_listing_payload(
            source_url="https://example.com/never-tried",
            created_at=now + timedelta(seconds=2),
            updated_at=now + timedelta(seconds=2),
        )
    )
    db_session.add_all([recently_failed, long_failed, never_tried])
    await db_session.commit()

    db_session.add(ImodigiExport(listing_id=recently_failed.id, status="failed"))
    db_session.add(ImodigiExport(listing_id=long_failed.id, status="failed"))
    await db_session.commit()

    # Push long_failed's last attempt outside the cooldown window so it's
    # eligible for a retry again, while recently_failed stays inside it.
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.imodigi_failed_retry_cooldown_minutes + 10
    )
    await db_session.execute(
        ImodigiExport.__table__.update()
        .where(ImodigiExport.listing_id == long_failed.id)
        .values(updated_at=cutoff)
    )
    await db_session.commit()

    result_ids = await get_listing_ids_for_bulk_imodigi(db_session, listing_ids=[], limit=10)

    assert recently_failed.id not in result_ids
    assert long_failed.id in result_ids
    assert never_tried.id in result_ids
