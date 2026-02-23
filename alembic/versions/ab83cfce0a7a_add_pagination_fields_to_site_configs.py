"""add pagination fields to site_configs

Revision ID: ab83cfce0a7a
Revises: 004_add_job_logs_urls
Create Date: 2026-02-23 09:40:52.534237
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ab83cfce0a7a"
down_revision: Union[str, None] = "004_add_job_logs_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add missing pagination fields to match the SQLAlchemy model.
    # Keep them nullable to avoid failing on existing rows.
    op.add_column(
        "site_configs",
        sa.Column("pagination_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "site_configs",
        sa.Column("pagination_param", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("site_configs", "pagination_param")
    op.drop_column("site_configs", "pagination_type")