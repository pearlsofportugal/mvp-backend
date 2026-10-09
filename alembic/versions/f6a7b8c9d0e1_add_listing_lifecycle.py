"""Add listing lifecycle columns (status, first_seen_at, last_seen_at, removed_at)

Purely additive and fast (nullable columns + a constant server default), so it
is safe to run at container start while the previous revision is still serving.
Existing rows become `active`; `first_seen_at`/`last_seen_at` are seeded from
`created_at`/`updated_at` in a single UPDATE.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-08 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'listings',
        sa.Column('status', sa.String(length=10), nullable=False, server_default='active'),
    )
    op.add_column('listings', sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('listings', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('listings', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_listings_status', 'listings', ['status'])
    op.execute("UPDATE listings SET first_seen_at = created_at, last_seen_at = updated_at")


def downgrade() -> None:
    op.drop_index('ix_listings_status', table_name='listings')
    op.drop_column('listings', 'removed_at')
    op.drop_column('listings', 'last_seen_at')
    op.drop_column('listings', 'first_seen_at')
    op.drop_column('listings', 'status')
