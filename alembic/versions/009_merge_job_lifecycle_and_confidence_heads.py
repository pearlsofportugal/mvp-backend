"""merge job lifecycle and confidence score heads

Revision ID: 009_merge_heads
Revises: 006_add_job_lifecycle_fields, 008_confidence_scores
Create Date: 2026-03-16
"""

from typing import Sequence, Union


revision: str = "009_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "006_add_job_lifecycle_fields",
    "008_confidence_scores",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass