"""Partner profile on site_configs and a keyset index for the contract v2 feed

Contract v2 exposes a `partner` object (name, logo, legal name, AMI licence, contacts) per source.
Those columns are optional and filled through PATCH /api/v1/sites/{key}. The feed paginates by
cursor over (updated_at, id), so listings get an index on exactly that pair.

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-10-22 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_COLUMNS = (
    ('logo_url', 2048),
    ('legal_name', 200),
    ('ami_licence', 50),
    ('website', 2048),
    ('phone', 50),
    ('email', 255),
    ('address', 500),
)


def upgrade() -> None:
    for name, length in PROFILE_COLUMNS:
        op.add_column('site_configs', sa.Column(name, sa.String(length=length), nullable=True))
    op.create_index('ix_listings_updated_at_id', 'listings', ['updated_at', 'id'])


def downgrade() -> None:
    op.drop_index('ix_listings_updated_at_id', table_name='listings')
    for name, _ in reversed(PROFILE_COLUMNS):
        op.drop_column('site_configs', name)
