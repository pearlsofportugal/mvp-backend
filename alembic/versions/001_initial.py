"""Initial migration — create all tables.

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── site_configs ──
    op.create_table(
        "site_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("selectors", JSONB, nullable=False, server_default="{}"),
        sa.Column("extraction_mode", sa.String(20), nullable=False, server_default="direct"),
        sa.Column("link_pattern", sa.String(500), nullable=True),
        sa.Column("image_filter", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_site_configs_key", "site_configs", ["key"])

    # ── scrape_jobs ──
    op.create_table(
        "scrape_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_key", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=True),
        sa.Column("start_url", sa.String(2048), nullable=False),
        sa.Column("max_pages", sa.Integer, nullable=False, server_default="10"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress", JSONB, nullable=True),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scrape_jobs_site_key", "scrape_jobs", ["site_key"])
    op.create_index("ix_scrape_jobs_status", "scrape_jobs", ["status"])

    # ── listings ──
    op.create_table(
        "listings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("partner_id", sa.String(255), nullable=True),
        sa.Column("source_partner", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True, unique=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("listing_type", sa.String(20), nullable=True),
        sa.Column("property_type", sa.String(50), nullable=True),
        sa.Column("typology", sa.String(10), nullable=True),
        sa.Column("bedrooms", sa.Integer, nullable=True),
        sa.Column("bathrooms", sa.Integer, nullable=True),
        sa.Column("floor", sa.String(20), nullable=True),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_currency", sa.String(3), nullable=True, server_default="EUR"),
        sa.Column("price_per_m2", sa.Numeric(10, 2), nullable=True),
        sa.Column("area_useful_m2", sa.Float, nullable=True),
        sa.Column("area_gross_m2", sa.Float, nullable=True),
        sa.Column("area_land_m2", sa.Float, nullable=True),
        sa.Column("district", sa.String(100), nullable=True),
        sa.Column("county", sa.String(100), nullable=True),
        sa.Column("parish", sa.String(100), nullable=True),
        sa.Column("full_address", sa.String(500), nullable=True),
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        sa.Column("has_garage", sa.Boolean, nullable=True),
        sa.Column("has_elevator", sa.Boolean, nullable=True),
        sa.Column("has_balcony", sa.Boolean, nullable=True),
        sa.Column("has_air_conditioning", sa.Boolean, nullable=True),
        sa.Column("has_pool", sa.Boolean, nullable=True),
        sa.Column("energy_certificate", sa.String(10), nullable=True),
        sa.Column("construction_year", sa.Integer, nullable=True),
        sa.Column("advertiser", sa.String(255), nullable=True),
        sa.Column("contacts", sa.String(500), nullable=True),
        sa.Column("raw_description", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enriched_description", sa.Text, nullable=True),
        sa.Column("description_quality_score", sa.Integer, nullable=True),
        sa.Column("meta_description", sa.Text, nullable=True),
        sa.Column("page_title", sa.String(500), nullable=True),
        sa.Column("headers", JSONB, nullable=True),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.Column("search_vector", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("scrape_job_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_listings_source_partner", "listings", ["source_partner"])
    op.create_index("ix_listings_district", "listings", ["district"])
    op.create_index("ix_listings_county", "listings", ["county"])
    op.create_index("ix_listings_property_type", "listings", ["property_type"])
    op.create_index("ix_listings_typology", "listings", ["typology"])
    op.create_index("ix_listings_price_amount", "listings", ["price_amount"])
    op.create_index("ix_listings_area_useful_m2", "listings", ["area_useful_m2"])
    op.create_index("ix_listings_source_partner_partner_id", "listings", ["source_partner", "partner_id"])
    op.create_index("ix_listings_created_at", "listings", ["created_at"])
    op.create_index("ix_listings_scrape_job_id", "listings", ["scrape_job_id"])

    # ── media_assets ──
    op.create_table(
        "media_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("listing_id", UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column("type", sa.String(20), nullable=True),
        sa.Column("position", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_media_assets_listing_id", "media_assets", ["listing_id"])

    # ── price_history ──
    op.create_table(
        "price_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("listing_id", UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_price_history_listing_id", "price_history", ["listing_id"])

    # ── Full-text search trigger (PostgreSQL) ──
    # Create a tsvector column and trigger for listings
    op.execute("""
        ALTER TABLE listings
        ALTER COLUMN search_vector TYPE tsvector
        USING to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''));
    """)
    op.execute("""
        CREATE INDEX ix_listings_search_vector ON listings USING GIN (search_vector);
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION listings_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('english', coalesce(NEW.title, '') || ' ' || coalesce(NEW.description, '') || ' ' || coalesce(NEW.enriched_description, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER listings_search_vector_trigger
        BEFORE INSERT OR UPDATE ON listings
        FOR EACH ROW EXECUTE FUNCTION listings_search_vector_update();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS listings_search_vector_trigger ON listings;")
    op.execute("DROP FUNCTION IF EXISTS listings_search_vector_update();")
    op.drop_index("ix_listings_search_vector")
    op.drop_table("price_history")
    op.drop_table("media_assets")
    op.drop_table("listings")
    op.drop_table("scrape_jobs")
    op.drop_table("site_configs")
