"""Add listings.typology_extra

Keeps the "+1" of a T1+1 typology, which the closed typology vocabulary
(T0, T1, T2, ...) would otherwise drop. Existing rows are re-normalised by
``python -m scripts.backfill_vocabularies`` (dry-run by default).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('typology_extra', sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'typology_extra')
