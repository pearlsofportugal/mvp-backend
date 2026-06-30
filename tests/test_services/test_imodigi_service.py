from datetime import datetime, timedelta, timezone

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
