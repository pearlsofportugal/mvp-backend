"""Seed initial site configurations.

Revision ID: 002_seed_site_configs
Revises: 001_initial
Create Date: 2026-02-09
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002_seed_site_configs"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEEDS = [
]


def upgrade() -> None:
    table = sa.table(
        "site_configs",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("base_url", sa.String),
        sa.column("selectors", sa.JSON),
        sa.column("extraction_mode", sa.String),
        sa.column("link_pattern", sa.String),
        sa.column("image_filter", sa.String),
        sa.column("is_active", sa.Boolean),
    )

    for seed in SEEDS:
        op.execute(
            table.insert().values(
                key=seed["key"],
                name=seed["name"],
                base_url=seed["base_url"],
                selectors=seed["selectors"],
                extraction_mode=seed["extraction_mode"],
                link_pattern=seed.get("link_pattern"),
                image_filter=seed.get("image_filter"),
                is_active=True,
            )
        )


def downgrade() -> None:
    for seed in SEEDS:
        op.execute(
            sa.text("DELETE FROM site_configs WHERE key = :key").bindparams(key=seed["key"])
        )
