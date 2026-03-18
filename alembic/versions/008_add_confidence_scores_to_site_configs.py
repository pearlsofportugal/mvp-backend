"""add confidence_scores to site_configs

Revision ID: 008_confidence_scores
Revises: 007_add_request_headers
Create Date: 2026-03-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "008_confidence_scores"
down_revision: Union[str, None] = "007_add_request_headers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_configs",
        sa.Column(
            "confidence_scores",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("site_configs", "confidence_scores", server_default=None)


def downgrade() -> None:
    op.drop_column("site_configs", "confidence_scores")
