"""Add request_headers column to site_configs if not exists.

Migration 007 was originally a no-op stub (the file was lost and reconstructed).
This means the column was never actually created in databases that ran the stub.
This migration repairs that by adding the column idempotently.

Revision ID: 022_fix_request_headers
Revises: 021_add_schedule_fields
Create Date: 2026-04-22
"""
from alembic import op

revision = "022_fix_request_headers"
down_revision = "021_add_schedule_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE site_configs ADD COLUMN IF NOT EXISTS request_headers JSONB"
    )


def downgrade() -> None:
    pass
