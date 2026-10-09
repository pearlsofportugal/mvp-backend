"""Add listings.description_clean

Display-ready description (agency boilerplate removed, formatting repaired).
``description`` is left untouched. Existing rows are filled by
``python -m scripts.backfill_description_clean`` (dry-run by default).

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-09 00:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('description_clean', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'description_clean')
