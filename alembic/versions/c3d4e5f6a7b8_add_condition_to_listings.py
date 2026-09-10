"""Add condition to listings

`condition` was extracted by several partner normalizers (habinedita,
mysquare, realkey, and any EGO-platform site whose selectors map "estado")
purely to derive the `is_new_construction` flag, but was never persisted as
its own column. As a side effect, confidence.py's "condition" field always
scored 0% for every partner — the Listing model had no such attribute to
read. This adds the column and wires it through the shared
_build_base_schema so it round-trips for every partner going forward.

Revision ID: c3d4e5f6a7b8
Revises: b2d3e4f5a6b7
Create Date: 2026-08-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('condition', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'condition')
