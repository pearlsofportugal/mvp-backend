"""Add request_headers column (stub — migration already applied to DB).

The original migration file was lost. This stub exists solely to satisfy
Alembic's revision chain so that subsequent migrations can run.

Revision ID: 007_add_request_headers
Revises: ab83cfce0a7a
Create Date: 2026-03-04 (reconstructed)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_add_request_headers"
down_revision: Union[str, None] = "ab83cfce0a7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use ADD COLUMN IF NOT EXISTS so this is safe whether the column already
    # exists (original DB) or doesn't yet (fresh Cloud Run deployment).
    op.execute(
        "ALTER TABLE site_configs ADD COLUMN IF NOT EXISTS request_headers JSONB"
    )


def downgrade() -> None:
    # Cannot safely undo — original migration body is unknown.
    raise NotImplementedError(
        "Downgrade for 007_add_request_headers is not available "
        "(original migration file was lost)."
    )
