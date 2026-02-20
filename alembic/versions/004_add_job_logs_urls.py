"""Add logs and urls columns to scrape_jobs.

Revision ID: 004_add_job_logs_urls
Revises: 003_config_tables
Create Date: 2026-02-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision: str = "004_add_job_logs_urls"
down_revision: Union[str, None] = "003_config_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add logs and urls columns to scrape_jobs table."""
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "logs",
            JSON,
            nullable=True,
            comment='{"errors": [], "warnings": [], "info": []}',
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "urls",
            JSON,
            nullable=True,
            comment='{"found": [], "scraped": [], "failed": []}',
        ),
    )


def downgrade() -> None:
    """Remove logs and urls columns from scrape_jobs table."""
    op.drop_column("scrape_jobs", "urls")
    op.drop_column("scrape_jobs", "logs")
