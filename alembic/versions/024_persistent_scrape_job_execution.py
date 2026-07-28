"""Persist scrape job dispatch metadata and append-only events.

Revision ID: 024_persistent_scrape_jobs
Revises: 023_add_performance_indexes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "024_persistent_scrape_jobs"
down_revision = "023_add_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scrape_jobs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("scrape_jobs", sa.Column("execution_id", sa.String(length=512), nullable=True))
    op.add_column("scrape_jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scrape_jobs", sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scrape_jobs", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_scrape_jobs_idempotency_key", "scrape_jobs", ["idempotency_key"])
    op.create_table(
        "scrape_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scrape_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scrape_job_events_event_type", "scrape_job_events", ["event_type"])
    op.create_index("ix_scrape_job_events_job_created", "scrape_job_events", ["scrape_job_id", "created_at"])
    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_background_jobs_job_type", "background_jobs", ["job_type"])
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_job_type", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.drop_index("ix_scrape_job_events_job_created", table_name="scrape_job_events")
    op.drop_index("ix_scrape_job_events_event_type", table_name="scrape_job_events")
    op.drop_table("scrape_job_events")
    op.drop_constraint("uq_scrape_jobs_idempotency_key", "scrape_jobs", type_="unique")
    op.drop_column("scrape_jobs", "dispatched_at")
    op.drop_column("scrape_jobs", "dispatch_attempts")
    op.drop_column("scrape_jobs", "attempt_count")
    op.drop_column("scrape_jobs", "execution_id")
    op.drop_column("scrape_jobs", "idempotency_key")
