"""Add lifecycle tracking fields to scrape_jobs.

Revision ID: 006_add_job_lifecycle_fields
Revises: 005_add_job_updated_at
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_add_job_lifecycle_fields"
down_revision: Union[str, None] = "005_add_job_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add heartbeat and cancellation tracking columns to scrape_jobs."""
    op.add_column(
        "scrape_jobs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_scrape_jobs_last_heartbeat_at",
        "scrape_jobs",
        ["last_heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove lifecycle tracking columns from scrape_jobs."""
    op.drop_index("ix_scrape_jobs_last_heartbeat_at", table_name="scrape_jobs")
    op.drop_column("scrape_jobs", "cancel_requested_at")
    op.drop_column("scrape_jobs", "last_heartbeat_at")