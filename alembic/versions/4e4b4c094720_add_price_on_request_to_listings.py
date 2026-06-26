"""add price_on_request to listings

Revision ID: 4e4b4c094720
Revises: 95b4d1ffd757
Create Date: 2026-06-15 16:42:34.630195
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4e4b4c094720'
down_revision: Union[str, None] = '95b4d1ffd757'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('listings', sa.Column('price_on_request', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'price_on_request')