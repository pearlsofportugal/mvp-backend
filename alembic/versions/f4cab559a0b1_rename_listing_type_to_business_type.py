"""rename listing_type to business_type

Revision ID: f4cab559a0b1
Revises: 6ff4de5cc6b5
Create Date: 2026-06-30 11:28:10.712374
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4cab559a0b1'
down_revision: Union[str, None] = '6ff4de5cc6b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'listings',
        'listing_type',
        new_column_name='business_type',
        existing_type=sa.String(length=20),
        existing_nullable=True,
        existing_comment='sale, rent',
    )


def downgrade() -> None:
    op.alter_column(
        'listings',
        'business_type',
        new_column_name='listing_type',
        existing_type=sa.String(length=20),
        existing_nullable=True,
        existing_comment='sale, rent',
    )