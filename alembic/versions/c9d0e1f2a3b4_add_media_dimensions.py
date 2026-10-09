"""Add media_assets.width / height

Nullable: dimensions are only stored when the source exposes them (srcset width
descriptors, og:image:width/height, WordPress-style "-1024x768" suffixes).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-08 00:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('media_assets', sa.Column('width', sa.Integer(), nullable=True))
    op.add_column('media_assets', sa.Column('height', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('media_assets', 'height')
    op.drop_column('media_assets', 'width')
