"""merge persistent scrape jobs and b613f8316be4

Revision ID: 5718027acc14
Revises: 024_persistent_scrape_jobs, b613f8316be4
Create Date: 2026-07-28 15:39:34.833327
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5718027acc14'
down_revision: Union[str, None] = ('024_persistent_scrape_jobs', 'b613f8316be4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
