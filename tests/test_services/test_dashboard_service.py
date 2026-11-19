"""Dashboard partner stats must only count active listings (same basis as /listings/stats)."""
import pytest

from app.core.lifecycle import STATUS_ACTIVE, STATUS_REMOVED
from app.models.listing_model import Listing
from app.models.site_config_model import SiteConfig
from app.services.dashboard_service import DashboardService


def _site(key: str) -> SiteConfig:
    return SiteConfig(
        key=key,
        name=key.title(),
        base_url=f"https://{key}.example.com",
        selectors={},
    )


def _listing(partner: str, n: int, status: str, price: float) -> Listing:
    return Listing(
        source_partner=partner,
        source_url=f"https://{partner}.example.com/{status}-{n}",
        title=f"{status} {n}",
        price_amount=price,
        status=status,
    )


@pytest.mark.asyncio
async def test_partner_stats_ignore_removed_listings(db_session):
    db_session.add_all([
        _site("alpha"),
        _site("empty"),
        _listing("alpha", 1, STATUS_ACTIVE, 100_000),
        _listing("alpha", 2, STATUS_ACTIVE, 300_000),
        _listing("alpha", 3, STATUS_REMOVED, 9_000_000),
    ])
    await db_session.commit()

    result = await DashboardService.get_partner_stats(db_session)
    by_key = {p.site.key: p for p in result.partners}

    assert by_key["alpha"].total_listings == 2
    assert float(by_key["alpha"].avg_price) == 200_000
    assert float(by_key["alpha"].max_price) == 300_000
    # A partner with no active listings still appears, with zero.
    assert by_key["empty"].total_listings == 0
