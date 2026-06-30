"""convert_site_configs_json_to_jsonb

Revision ID: e0da3ce51061
Revises: 4e4b4c094720
Create Date: 2026-06-18 11:53:22.850157
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0da3ce51061"
down_revision: Union[str, None] = "4e4b4c094720"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE site_configs
            ALTER COLUMN selectors
                TYPE jsonb USING selectors::jsonb,

            ALTER COLUMN confidence_scores
                TYPE jsonb USING confidence_scores::jsonb,

            ALTER COLUMN request_headers
                TYPE jsonb USING request_headers::jsonb,

            ALTER COLUMN image_filter
                TYPE jsonb USING to_jsonb(image_filter),

            ALTER COLUMN image_exclude_filter
                TYPE jsonb USING to_jsonb(image_exclude_filter);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE site_configs
            ALTER COLUMN selectors
                TYPE json USING selectors::json,

            ALTER COLUMN confidence_scores
                TYPE json USING confidence_scores::json,

            ALTER COLUMN request_headers
                TYPE json USING request_headers::json,

            ALTER COLUMN image_filter
                TYPE varchar USING image_filter #>> '{}',

            ALTER COLUMN image_exclude_filter
                TYPE varchar USING image_exclude_filter #>> '{}';
    """)