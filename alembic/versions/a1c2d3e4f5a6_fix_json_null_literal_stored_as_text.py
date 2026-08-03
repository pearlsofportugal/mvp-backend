"""Fix JSON 'null' literal stored instead of SQL NULL

SQLAlchemy's JSON type persists a Python None as the JSON literal 'null'
(text) instead of SQL NULL unless the column is declared with
none_as_null=True. Several nullable JSON columns were declared without that
flag, so any code path that explicitly assigned None (e.g. resetting
enriched_translations) silently wrote the 3-character string 'null' instead
of a real NULL. Filters that check `column IS NULL` (e.g. the `is_enriched`
listing filter) then failed to match those rows.

The model has been fixed to use JSON(none_as_null=True) going forward; this
migration repairs rows already affected.

Revision ID: a1c2d3e4f5a6
Revises: 5718027acc14
Create Date: 2026-08-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c2d3e4f5a6'
down_revision: Union[str, None] = '5718027acc14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LISTING_COLUMNS = ("enriched_translations", "headers", "raw_payload")


def upgrade() -> None:
    for column in _LISTING_COLUMNS:
        op.execute(
            f"UPDATE listings SET {column} = NULL "
            f"WHERE {column} IS NOT NULL AND {column}::text = 'null'"
        )
    op.execute(
        "UPDATE site_configs SET request_headers = NULL "
        "WHERE request_headers IS NOT NULL AND request_headers::text = 'null'"
    )


def downgrade() -> None:
    # Data repair only — the corrupted JSON-'null' representation is not
    # worth restoring, and the previous state is indistinguishable from a
    # genuine SQL NULL that predates the bug.
    pass
