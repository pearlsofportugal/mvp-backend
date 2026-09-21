"""Add confidence_not_applicable_fields to site_configs

confidence.py scores the same fixed set of fields (price, title, area, rooms,
location, images, property_type, typology, condition, business_type,
land_area) for every partner. Some fields structurally never apply to a given
partner — e.g. 'typology' for an English-language site that has no T-code
convention at all (bpaproperty) — which permanently drags that partner's
average down even though nothing is actually broken. This column lets a
SiteConfig opt specific fields out of scoring entirely, so the average
reflects fields that are genuinely achievable for that partner.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'site_configs',
        sa.Column('confidence_not_applicable_fields', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('site_configs', 'confidence_not_applicable_fields')
