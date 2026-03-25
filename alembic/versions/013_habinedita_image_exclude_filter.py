"""Set image_exclude_filter on habinedita site config to block banner images.

The image https://mediamasterhabinedita.ximo.pt/admin/1/FOTOS/... is a site-wide
banner ("Imóveis Show") that must be excluded from listing image extraction.

Revision ID: 013_habinedita_image_exclude_filter
Revises: 012_add_image_exclude_filter
Create Date: 2026-03-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013_habinedita_img_excl"
down_revision: Union[str, None] = "012_add_image_exclude_filter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches any image served from the /admin/ path on the habinedita media domain.
# Example: https://mediamasterhabinedita.ximo.pt/admin/1/FOTOS/110/5508033369969t.jpg
_EXCLUDE_PATTERN = r"mediamasterhabinedita\.ximo\.pt/admin/"


def upgrade() -> None:
    site_configs = sa.table(
        "site_configs",
        sa.column("key", sa.String),
        sa.column("image_exclude_filter", sa.String),
    )
    op.execute(
        site_configs.update()
        .where(site_configs.c.key == "habinedita")
        .values(image_exclude_filter=_EXCLUDE_PATTERN)
    )


def downgrade() -> None:
    site_configs = sa.table(
        "site_configs",
        sa.column("key", sa.String),
        sa.column("image_exclude_filter", sa.String),
    )
    op.execute(
        site_configs.update()
        .where(site_configs.c.key == "habinedita")
        .values(image_exclude_filter=None)
    )
