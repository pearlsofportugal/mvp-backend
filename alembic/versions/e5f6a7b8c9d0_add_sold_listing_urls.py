"""Add sold_listing_urls

Some partner sitemaps (e.g. habita) never remove sold/reserved listings, so
every scrape job re-pays the full fetch cost (ethical delay + JS render) just
to rediscover the same dead URL — confirmed via job logs to be the exact same
set of URLs across consecutive habita scrapes. This table lets the scraper
skip a URL outright once it was recently confirmed sold, instead of
re-fetching it every run. Entries are revalidated periodically (not skipped
forever) in case a listing returns to the market.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-04 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sold_listing_urls',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('source_partner', sa.String(length=50), nullable=False),
        sa.Column('source_url', sa.String(length=1000), nullable=False),
        sa.Column('last_confirmed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_sold_listing_urls_source_partner', 'sold_listing_urls', ['source_partner'])
    op.create_index(
        'ix_sold_listing_urls_source_url', 'sold_listing_urls', ['source_url'], unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_sold_listing_urls_source_url', table_name='sold_listing_urls')
    op.drop_index('ix_sold_listing_urls_source_partner', table_name='sold_listing_urls')
    op.drop_table('sold_listing_urls')
