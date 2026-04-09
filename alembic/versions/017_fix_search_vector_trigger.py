"""Fix listings_search_vector_update trigger — remove enriched_description reference.

Revision ID: 017_fix_search_vector_trigger
Revises: 016_drop_flat_enriched_columns
Create Date: 2026-04-08

After migration 016 dropped enriched_description, the trigger function still
referenced NEW.enriched_description, causing UndefinedColumnError on any INSERT
or UPDATE to the listings table.
"""

revision: str = "017_fix_search_vector_trigger"
down_revision: str = "016_drop_flat_enriched_columns"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION listings_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('english',
                    coalesce(NEW.title, '') || ' ' ||
                    coalesce(NEW.description, '')
                );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION listings_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('english',
                    coalesce(NEW.title, '') || ' ' ||
                    coalesce(NEW.description, '') || ' ' ||
                    coalesce(NEW.enriched_description, '')
                );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
