"""Drop legacy flat enriched_* columns from listings.

Enrichment content is now stored in enriched_translations (JSON) keyed by locale.
The three old EN-only columns are no longer used.

WARNING: This migration is destructive. Any existing enriched_title,
enriched_description, and enriched_meta_description values will be permanently
deleted. Back up the table before applying in production.

Revision ID: 016_drop_flat_enriched_columns
Revises: 015_add_enriched_translations
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016_drop_flat_enriched_columns"
down_revision: str = "015_add_enriched_translations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("listings", "enriched_title")
    op.drop_column("listings", "enriched_description")
    op.drop_column("listings", "enriched_meta_description")


def downgrade() -> None:
    op.add_column("listings", sa.Column("enriched_meta_description", sa.Text(), nullable=True))
    op.add_column("listings", sa.Column("enriched_description", sa.Text(), nullable=True))
    op.add_column("listings", sa.Column("enriched_title", sa.String(length=500), nullable=True))
