"""convert image filters back to text

Revision ID: 5bc0b4733832
Revises: d0995c1e09fa
Create Date: 2026-07-02 13:10:22.042225
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5bc0b4733832'
down_revision: Union[str, None] = 'd0995c1e09fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("""
        ALTER TABLE site_configs
            ALTER COLUMN image_filter
                TYPE varchar(500)
                USING trim(both '"' from image_filter::text),

            ALTER COLUMN image_exclude_filter
                TYPE varchar(500)
                USING trim(both '"' from image_exclude_filter::text);
    """)


def downgrade():
    op.execute("""
        ALTER TABLE site_configs
            ALTER COLUMN image_filter
                TYPE jsonb
                USING to_jsonb(image_filter),

            ALTER COLUMN image_exclude_filter
                TYPE jsonb
                USING to_jsonb(image_exclude_filter);
    """)