"""add partner_id to imodigi_exports

Revision ID: 95b4d1ffd757
Revises: 023_add_performance_indexes
Create Date: 2026-06-05 11:38:18.720091
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '95b4d1ffd757'
down_revision: Union[str, None] = '023_add_performance_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "imodigi_exports",
        sa.Column(
            "partner_id",
            sa.String(length=100),
            nullable=True,
            comment="partner_id / external reference do listing no momento do export",
        ),
    )


def downgrade() -> None:
    op.drop_column("imodigi_exports", "partner_id")