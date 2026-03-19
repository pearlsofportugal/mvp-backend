"""add enriched_title to listings

Revision ID: 010_add_enriched_title
Revises: 009_merge_heads
Create Date: 2026-03-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_add_enriched_title"
down_revision: Union[str, None] = "009_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("enriched_title", sa.String(500), nullable=True, comment="AI-enriched SEO title"),
    )


def downgrade() -> None:
    op.drop_column("listings", "enriched_title")
