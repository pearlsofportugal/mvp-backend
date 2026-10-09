"""Add listings.content_hash

Nullable on purpose: rows scraped before this revision have no hash. The
scraper compares against a hash recomputed from the stored row, so the first
re-scrape of an unchanged listing is not reported as a change; run
``python -m scripts.backfill_content_hash`` to fill the column ahead of time.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-08 00:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('content_hash', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'content_hash')
