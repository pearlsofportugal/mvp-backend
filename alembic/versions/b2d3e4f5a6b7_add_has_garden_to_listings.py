"""Add has_garden to listings

The parser already supported a per-site `garden_selector` and a "garden"
raw field, but nothing downstream (schema, mapper, DB column) ever read it —
extracted garden data was silently dropped. This adds the missing column so
the feature flag survives through to the API, mirroring has_pool etc.

Revision ID: b2d3e4f5a6b7
Revises: a1c2d3e4f5a6
Create Date: 2026-08-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2d3e4f5a6b7'
down_revision: Union[str, None] = 'a1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('has_garden', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'has_garden')
