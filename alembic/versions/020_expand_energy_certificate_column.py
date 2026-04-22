"""Expand listings.energy_certificate from VARCHAR(10) to VARCHAR(20).

Values such as 'Unavailable', 'Isento', 'Não disponível' exceed the original
10-character limit and caused StringDataRightTruncationError at runtime.

Revision ID: 020_expand_energy_certificate
Revises: 019_fix_search_vector_language
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "020_expand_energy_certificate"
down_revision = "019_fix_search_vector_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "listings",
        "energy_certificate",
        existing_type=sa.String(10),
        type_=sa.String(20),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Truncate any values that exceed 10 chars before shrinking
    op.execute(
        "UPDATE listings SET energy_certificate = LEFT(energy_certificate, 10) "
        "WHERE LENGTH(energy_certificate) > 10"
    )
    op.alter_column(
        "listings",
        "energy_certificate",
        existing_type=sa.String(20),
        type_=sa.String(10),
        existing_nullable=True,
    )
