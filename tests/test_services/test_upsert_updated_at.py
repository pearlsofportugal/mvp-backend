"""`updated_at` must only move when the listing's content changes."""
from uuid import uuid4

from sqlalchemy import select

from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.models.price_history_model import PriceHistory
from app.schemas.property_schema import Address, MediaAsset as MediaSchema, Money, PropertySchema
from app.services import scraper_service
from app.utils.content_hash import compute_content_hash

URL = "https://agency.pt/imovel/1"


def _schema(**kw) -> PropertySchema:
    base = dict(
        source_partner="pearls",
        source_url=URL,
        partner_id="REF-1",
        title="Apartamento T2 no Porto",
        property_type="Apartamento",
        typology="T2",
        business_type="sale",
        price=Money(amount=250000, currency="EUR"),
        area_useful_m2=80.0,
        address=Address(region="Porto", city="Porto", area="Lordelo do Ouro"),
        media=[MediaSchema(url="https://cdn.pt/1.jpg", type="photo", position=0),
               MediaSchema(url="https://cdn.pt/2.jpg", type="photo", position=1)],
        descriptions={"raw": "Bonito apartamento.", "pt": "Bonito apartamento."},
    )
    base.update(kw)
    return PropertySchema(**base)


async def _persist(db, schema) -> bool:
    is_new = await scraper_service._persist_listing(db, str(uuid4()), schema, "pearls")
    await db.commit()
    return is_new


async def _row(db) -> Listing:
    db.expire_all()
    return (await db.execute(select(Listing).where(Listing.source_url == URL))).scalar_one()


async def _media_urls(db) -> list[str]:
    rows = await db.execute(select(MediaAsset.url).order_by(MediaAsset.position))
    return list(rows.scalars().all())


async def test_same_scrape_twice_does_not_touch_updated_at(db_session):
    assert await _persist(db_session, _schema()) is True
    first = await _row(db_session)
    updated_at, seen_at, hash_ = first.updated_at, first.last_seen_at, first.content_hash
    assert hash_ and first.first_seen_at is not None

    assert await _persist(db_session, _schema()) is False
    second = await _row(db_session)

    assert second.updated_at == updated_at
    assert second.content_hash == hash_
    assert second.last_seen_at > seen_at          # the visit is still recorded
    assert await _media_urls(db_session) == ["https://cdn.pt/1.jpg", "https://cdn.pt/2.jpg"]


async def test_real_change_moves_updated_at_and_records_price_history(db_session):
    await _persist(db_session, _schema())
    before = await _row(db_session)
    before_updated_at, before_hash = before.updated_at, before.content_hash  # _row returns the same instance

    await _persist(db_session, _schema(price=Money(amount=240000, currency="EUR")))
    after = await _row(db_session)

    assert after.updated_at > before_updated_at
    assert after.content_hash != before_hash
    assert float(after.price_amount) == 240000
    history = (await db_session.execute(select(PriceHistory))).scalars().all()
    assert [float(h.price_amount) for h in history] == [250000]


async def test_new_photo_counts_as_a_change(db_session):
    await _persist(db_session, _schema())
    before_updated_at = (await _row(db_session)).updated_at
    photos = _schema().media + [MediaSchema(url="https://cdn.pt/3.jpg", type="photo", position=2)]
    await _persist(db_session, _schema(media=photos))
    after = await _row(db_session)
    assert after.updated_at > before_updated_at
    assert len(await _media_urls(db_session)) == 3


async def test_dropped_field_on_a_flaky_scrape_is_not_a_change(db_session):
    """None never erases a stored value, so it must not register as an update either."""
    await _persist(db_session, _schema())
    before_updated_at = (await _row(db_session)).updated_at
    await _persist(db_session, _schema(area_useful_m2=None))
    after = await _row(db_session)
    assert after.updated_at == before_updated_at
    assert after.area_useful_m2 == 80.0


async def test_legacy_row_without_hash_is_not_reported_as_changed(db_session):
    await _persist(db_session, _schema())
    row = await _row(db_session)
    row.content_hash = None                      # as stored before the column existed
    await db_session.commit()
    updated_at = (await _row(db_session)).updated_at

    await _persist(db_session, _schema())
    after = await _row(db_session)
    assert after.updated_at == updated_at
    assert after.content_hash is not None


async def test_removed_listing_that_returns_is_reactivated_and_bumped(db_session):
    await _persist(db_session, _schema())
    row = await _row(db_session)
    row.status = "removed"
    await db_session.commit()
    removed_updated_at = (await _row(db_session)).updated_at

    await _persist(db_session, _schema())
    after = await _row(db_session)
    assert after.status == "active" and after.removed_at is None
    assert after.updated_at > removed_updated_at


def test_hash_is_insensitive_to_noise_and_sensitive_to_content():
    base = {"title": "T2  no Porto", "price_amount": 250000, "area_useful_m2": 80.0}
    same = {"title": "T2 no Porto ", "price_amount": 250000.00, "area_useful_m2": 80.0000000001}
    assert compute_content_hash(base, ["a"]) == compute_content_hash(same, ["a"])
    assert compute_content_hash(base, ["a"]) != compute_content_hash(base, ["a", "b"])
    assert compute_content_hash(base, ["a", "b"]) != compute_content_hash(base, ["b", "a"])
    assert compute_content_hash(base) != compute_content_hash({**base, "title": "T3 no Porto"})


async def test_scrape_that_reverts_a_manual_edit_is_a_change(db_session):
    """PATCH edits the row without refreshing content_hash; a scrape undoing it must still register."""
    await _persist(db_session, _schema())
    row = await _row(db_session)
    row.title = "Edited by hand"
    await db_session.commit()
    edited_updated_at = (await _row(db_session)).updated_at

    await _persist(db_session, _schema())  # original title comes back
    after = await _row(db_session)
    assert after.title == "Apartamento T2 no Porto"
    assert after.updated_at > edited_updated_at
