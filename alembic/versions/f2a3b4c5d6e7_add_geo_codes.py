"""Add official geographic codes to listings

district_code (2 digits), county_code / DICO (4) and parish_code / DICOFRE (6).
The text columns district/county/parish are untouched. Existing rows are filled
by ``python -m scripts.backfill_geo_codes`` (dry-run by default).

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-09 00:20:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('district_code', sa.String(length=2), nullable=True))
    op.add_column('listings', sa.Column('county_code', sa.String(length=4), nullable=True))
    op.add_column('listings', sa.Column('parish_code', sa.String(length=6), nullable=True))
    op.create_index('ix_listings_district_code', 'listings', ['district_code'])
    op.create_index('ix_listings_county_code', 'listings', ['county_code'])
    op.create_index('ix_listings_parish_code', 'listings', ['parish_code'])


def downgrade() -> None:
    op.drop_index('ix_listings_parish_code', table_name='listings')
    op.drop_index('ix_listings_county_code', table_name='listings')
    op.drop_index('ix_listings_district_code', table_name='listings')
    op.drop_column('listings', 'parish_code')
    op.drop_column('listings', 'county_code')
    op.drop_column('listings', 'district_code')
