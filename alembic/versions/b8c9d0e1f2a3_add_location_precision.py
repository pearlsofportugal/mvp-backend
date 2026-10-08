"""Add listings.location_precision

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-08 00:20:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('location_precision', sa.String(length=10), nullable=True))
    # Rows that already carry coordinates (manual POST/PATCH) were not geocoded: treat them as exact.
    op.execute(
        "UPDATE listings SET location_precision = 'exact' "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column('listings', 'location_precision')
