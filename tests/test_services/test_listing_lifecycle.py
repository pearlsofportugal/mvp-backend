"""Lifecycle rules: junk pages, soft-delete and reactivation."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.adapters.http_adapter import HttpAdapter
from app.models.listing_model import Listing
from app.repositories.listings_repository import ListingRepository
from app.schemas.property_schema import Money, PropertySchema
from app.services.mapper_service import is_junk_listing, schema_to_listing_dict
from app.services import scraper_service


def _schema(**kw) -> PropertySchema:
    return PropertySchema(source_partner="pearls", source_url="https://x.pt/1", **kw)


@pytest.mark.parametrize("title", ["410", "404", " 410 ", None, ""])
def test_error_page_shell_is_junk(title):
    assert is_junk_listing(_schema(title=title))


def test_real_or_partial_listing_is_not_junk():
    assert not is_junk_listing(_schema(title="Apartamento T2", property_type="Apartamento"))
    # numeric title but a real price -> a (badly titled) listing, not a shell
    assert not is_junk_listing(_schema(title="1234", price=Money(amount=250000, currency="EUR")))
    assert not is_junk_listing(_schema(title="410", property_type="Moradia"))
    assert not is_junk_listing(_schema(title="Terreno", price_on_request=True))


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "<html></html>"


@pytest.mark.parametrize("status", [404, 410, 500])
def test_http_adapter_exposes_last_status(monkeypatch, status):
    adapter = HttpAdapter(user_agent="t", max_retries=0)
    monkeypatch.setattr(adapter._session, "get", lambda url, timeout: _Resp(status))
    assert adapter.get("https://x.pt/a") is None
    assert adapter.last_status == status


async def test_mark_removed_and_reactivate_on_rescrape(db_session):
    schema = _schema(title="Apartamento T2", property_type="Apartamento", price=Money(amount=100000, currency="EUR"))
    listing = Listing(source_partner="pearls", source_url="https://x.pt/1", title="Apartamento T2")
    db_session.add(listing)
    await db_session.commit()
    assert listing.status == "active" and listing.first_seen_at is not None

    assert await ListingRepository.mark_removed(db_session, listing) is True
    await db_session.commit()
    assert listing.status == "removed" and listing.removed_at is not None
    assert await ListingRepository.mark_removed(db_session, listing) is False  # idempotent

    data = schema_to_listing_dict(schema, scrape_job_id=uuid4())
    await scraper_service._persist_listing_legacy(db_session, str(uuid4()), schema, data)
    await db_session.commit()
    await db_session.refresh(listing)
    assert listing.status == "active"
    assert listing.removed_at is None


async def test_mark_missing_removed_is_scoped_to_partner_and_active(db_session):
    keep = Listing(source_partner="pearls", source_url="https://x.pt/keep", title="a")
    gone = Listing(source_partner="pearls", source_url="https://x.pt/gone", title="b")
    other = Listing(source_partner="other", source_url="https://y.pt/z", title="c")
    db_session.add_all([keep, gone, other])
    await db_session.commit()

    n = await ListingRepository.mark_missing_removed(db_session, "pearls", {"https://x.pt/keep"})
    await db_session.commit()
    assert n == 1
    db_session.expire_all()  # bulk UPDATE bypasses the identity map
    rows = {l.source_url: l.status for l in (await db_session.execute(select(Listing))).scalars()}
    assert rows == {"https://x.pt/keep": "active", "https://x.pt/gone": "removed", "https://y.pt/z": "active"}


async def test_archive_junk_listings_dry_run_then_apply(db_session, tmp_path):
    from app.services.listing_cleanup_service import archive_junk_listings

    shell = Listing(source_partner="pearls", source_url="https://x.pt/410", title="410")
    good = Listing(source_partner="pearls", source_url="https://x.pt/ok", title="Moradia T4", property_type="Moradia")
    numeric_but_priced = Listing(source_partner="pearls", source_url="https://x.pt/n", title="1234", price_amount=100000)
    db_session.add_all([shell, good, numeric_but_priced])
    await db_session.commit()
    backup = tmp_path / "b.csv"

    found = await archive_junk_listings(db_session, backup, apply=False)
    assert [f.source_url for f in found] == ["https://x.pt/410"]
    assert not backup.exists()
    assert shell.status == "active"

    await archive_junk_listings(db_session, backup, apply=True)
    db_session.expire_all()
    rows = {l.source_url: l.status for l in (await db_session.execute(select(Listing))).scalars()}
    assert rows == {"https://x.pt/410": "removed", "https://x.pt/ok": "active", "https://x.pt/n": "active"}
    assert "https://x.pt/410" in backup.read_text(encoding="utf-8")
