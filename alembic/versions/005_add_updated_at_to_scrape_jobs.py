"""Add updated_at column to scrape_jobs.

Revision ID: 005_add_updated_at_to_scrape_jobs
Revises: ab83cfce0a7a
Create Date: 2026-03-04
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005_add_job_updated_at"
down_revision: Union[str, None] = "007_add_request_headers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add updated_at column to scrape_jobs and backfill from created_at."""
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Backfill existing rows: set updated_at = created_at
    op.execute(
        "UPDATE scrape_jobs SET updated_at = created_at WHERE updated_at IS NULL"
    )
    # Make non-nullable now that all rows have a value
    op.alter_column("scrape_jobs", "updated_at", nullable=False)


def downgrade() -> None:
    """Remove updated_at column from scrape_jobs."""
    op.drop_column("scrape_jobs", "updated_at")
