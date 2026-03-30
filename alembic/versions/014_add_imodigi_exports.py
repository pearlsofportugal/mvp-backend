"""Add imodigi_exports table to track CRM export state per listing.

Revision ID: 014_add_imodigi_exports
Revises: 013_habinedita_image_exclude_filter
Create Date: 2026-01-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014_add_imodigi_exports"
down_revision: str = "013_habinedita_img_excl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imodigi_exports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("imodigi_property_id", sa.Integer(), nullable=True),
        sa.Column("imodigi_reference", sa.String(length=100), nullable=True),
        sa.Column("imodigi_client_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("listing_id", name="uq_imodigi_exports_listing_id"),
    )
    op.create_index(
        "ix_imodigi_exports_listing_id",
        "imodigi_exports",
        ["listing_id"],
        unique=False,
    )
    op.create_index(
        "ix_imodigi_exports_status",
        "imodigi_exports",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_imodigi_exports_status", table_name="imodigi_exports")
    op.drop_index("ix_imodigi_exports_listing_id", table_name="imodigi_exports")
    op.drop_table("imodigi_exports")
