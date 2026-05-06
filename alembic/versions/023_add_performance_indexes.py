"""Add GIN trigram indexes on listing text columns and composite index on imodigi_exports.

Merges the two open heads (022_fix_request_headers and ab83cfce0a7a).

GIN trigram indexes allow efficient ILIKE '%term%' queries on district/county/parish —
without them every filter call does a full sequential table scan.

The composite index on imodigi_exports(listing_id, status) accelerates the
correlated EXISTS subquery used by the is_exported_to_imodigi filter.

Revision ID: 023_add_performance_indexes
Revises: 022_fix_request_headers, ab83cfce0a7a
Create Date: 2026-05-06
"""
from alembic import op

revision = "023_add_performance_indexes"
down_revision = ("022_fix_request_headers", "ab83cfce0a7a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pg_trgm for trigram-based GIN indexes (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram indexes — turn leading-wildcard ILIKE into index seeks.
    # Note: CREATE INDEX without CONCURRENTLY runs inside Alembic's transaction;
    # CONCURRENTLY requires autocommit and is not compatible with transactional DDL.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listings_district_trgm "
        "ON listings USING gin (district gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listings_county_trgm "
        "ON listings USING gin (county gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_listings_parish_trgm "
        "ON listings USING gin (parish gin_trgm_ops)"
    )

    # Composite index for the is_exported_to_imodigi correlated EXISTS filter
    op.create_index(
        "ix_imodigi_exports_listing_status",
        "imodigi_exports",
        ["listing_id", "status"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_imodigi_exports_listing_status")
    op.execute("DROP INDEX IF EXISTS ix_listings_parish_trgm")
    op.execute("DROP INDEX IF EXISTS ix_listings_county_trgm")
    op.execute("DROP INDEX IF EXISTS ix_listings_district_trgm")
