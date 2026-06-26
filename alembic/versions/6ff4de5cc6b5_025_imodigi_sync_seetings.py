"""Sem alterações ao schema — settings geridos via env vars.

Este ficheiro existe apenas para documentar que as variáveis
IMODIGI_SYNC_ENABLED, IMODIGI_SYNC_INTERVAL_MINUTES e IMODIGI_SYNC_LIMIT
foram adicionadas ao config.py nesta versão.

Revision ID: 025_add_imodigi_sync_settings
Revises: 95b4d1ffd757
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ff4de5cc6b5'
down_revision: Union[str, None] = 'e0da3ce51061'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
