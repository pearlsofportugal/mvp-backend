"""add enriched_meta_description to listings

Revision ID: 011_add_enriched_meta_description
Revises: 010_add_enriched_title
Create Date: 2026-03-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_add_enriched_meta_desc"
down_revision: Union[str, None] = "010_add_enriched_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("enriched_meta_description", sa.Text, nullable=True, comment="AI-enriched meta description"),
    )


def downgrade() -> None:
    op.drop_column("listings", "enriched_meta_description")
