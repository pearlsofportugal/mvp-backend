"""Add image_exclude_filter column to site_configs.

Revision ID: 012_add_image_exclude_filter
Revises: 011_add_enriched_meta_desc
Create Date: 2026-03-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012_add_image_exclude_filter"
down_revision: Union[str, None] = "011_add_enriched_meta_desc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_configs",
        sa.Column("image_exclude_filter", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("site_configs", "image_exclude_filter")
