"""Fix listings_search_vector_update trigger — use 'portuguese' instead of 'english'.

Revision ID: 019_fix_search_vector_language
Revises: 018_add_use_js_render
Create Date: 2026-04-15

The trigger was using the 'english' text-search configuration. Since all
listing content is in Portuguese, switching to 'portuguese' improves stemming
quality (removes common stopwords such as 'de', 'da', 'do', etc.) and makes
``plainto_tsquery('portuguese', ...)`` searches in _filters.py actually match.
"""

revision: str = "019_fix_search_vector_language"
down_revision: str = "018_add_use_js_render"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION listings_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('portuguese',
                    coalesce(NEW.title, '') || ' ' ||
                    coalesce(NEW.description, '')
                );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Rebuild existing rows so they use the new language configuration.
    op.execute("""
        UPDATE listings
        SET search_vector =
            to_tsvector('portuguese',
                coalesce(title, '') || ' ' ||
                coalesce(description, '')
            );
    """)


def downgrade() -> None:
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

    op.execute("""
        UPDATE listings
        SET search_vector =
            to_tsvector('english',
                coalesce(title, '') || ' ' ||
                coalesce(description, '')
            );
    """)
