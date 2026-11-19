"""Add ai_usage_log

One row per Gemini call (tokens, cost when prices are configured, status), so AI spend can be seen
per day and capped. Purely additive: a new table, nothing existing is touched.

Revision ID: d1e2f3a4b5c6
Revises: b7c8d9e0f1a2
Create Date: 2026-11-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_usage_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('operation', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('listing_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_partner', sa.String(length=50), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('cost_eur', sa.Numeric(10, 6), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='ok'),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
    )
    op.create_index('ix_ai_usage_log_created_at', 'ai_usage_log', ['created_at'])
    op.create_index('ix_ai_usage_log_listing_id', 'ai_usage_log', ['listing_id'])
    op.create_index('ix_ai_usage_log_source_partner', 'ai_usage_log', ['source_partner'])


def downgrade() -> None:
    op.drop_index('ix_ai_usage_log_source_partner', table_name='ai_usage_log')
    op.drop_index('ix_ai_usage_log_listing_id', table_name='ai_usage_log')
    op.drop_index('ix_ai_usage_log_created_at', table_name='ai_usage_log')
    op.drop_table('ai_usage_log')
