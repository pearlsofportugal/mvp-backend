"""add use_js_render to site_configs

Revision ID: 018_add_use_js_render
Revises: 017_fix_search_vector_trigger
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa

revision = "018_add_use_js_render"
down_revision = "017_fix_search_vector_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "site_configs",
        sa.Column("use_js_render", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("site_configs", "use_js_render")
