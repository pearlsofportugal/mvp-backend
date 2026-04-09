"""Add enriched_translations JSON column to listings.

Stores multi-locale AI-generated SEO content keyed by locale code.
Structure: {"pt": {"title": ..., "description": ..., "meta_description": ...}, "es": {...}, ...}

Revision ID: 015_add_enriched_translations
Revises: 014_add_imodigi_exports
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_add_enriched_translations"
down_revision: str = "014_add_imodigi_exports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column(
            "enriched_translations",
            sa.JSON(),
            nullable=True,
            comment="AI-generated SEO content per locale: {pt: {title, description, meta_description}, es: {...}, ...}",
        ),
    )


def downgrade() -> None:
    op.drop_column("listings", "enriched_translations")
