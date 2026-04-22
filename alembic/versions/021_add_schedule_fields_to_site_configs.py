"""Add schedule fields to site_configs table.

Adds 6 nullable columns to support per-site cron scheduling:
  - schedule_enabled        — toggle scheduling on/off for this site
  - schedule_interval_minutes — how often to run (in minutes)
  - schedule_start_at       — when the first run should occur (NULL = immediately)
  - schedule_timezone       — IANA timezone string for the schedule
  - schedule_start_url      — URL to begin scraping (NULL = falls back to base_url)
  - schedule_max_pages      — pages to scrape per scheduled run (NULL = default_max_pages)

All columns are nullable or have server_default to ensure a non-destructive migration.

Revision ID: 021_add_schedule_fields_to_site_configs
Revises: 020_expand_energy_certificate
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa

revision = "021_add_schedule_fields"
down_revision = "020_expand_energy_certificate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "site_configs",
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "site_configs",
        sa.Column("schedule_interval_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "site_configs",
        sa.Column("schedule_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "site_configs",
        sa.Column(
            "schedule_timezone",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'Europe/Lisbon'"),
        ),
    )
    op.add_column(
        "site_configs",
        sa.Column("schedule_start_url", sa.String(2048), nullable=True),
    )
    op.add_column(
        "site_configs",
        sa.Column("schedule_max_pages", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("site_configs", "schedule_max_pages")
    op.drop_column("site_configs", "schedule_start_url")
    op.drop_column("site_configs", "schedule_timezone")
    op.drop_column("site_configs", "schedule_start_at")
    op.drop_column("site_configs", "schedule_interval_minutes")
    op.drop_column("site_configs", "schedule_enabled")
