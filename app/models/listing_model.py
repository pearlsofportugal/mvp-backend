"""Listing SQLAlchemy model — strongly typed real estate listing."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.media_model import MediaAsset
    from app.models.price_history_model import PriceHistory


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    partner_id: Mapped[Optional[str]] = mapped_column(String(255), comment="ID on the original site (e.g. REF-12345)")
    source_partner: Mapped[str] = mapped_column(String(50), index=True, comment="pearls")
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), unique=True, comment="Original listing URL (deduplication)")

    title: Mapped[Optional[str]] = mapped_column(String(500))
    listing_type: Mapped[Optional[str]] = mapped_column(String(20), comment="sale, rent")
    property_type: Mapped[Optional[str]] = mapped_column(String(50), comment="apartment, house, land, etc.")
    typology: Mapped[Optional[str]] = mapped_column(String(10), comment="T0, T1, T2, T3, etc.")
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer)
    bathrooms: Mapped[Optional[int]] = mapped_column(Integer)
    floor: Mapped[Optional[str]] = mapped_column(String(20))

    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), comment="Price in numeric form")
    price_currency: Mapped[Optional[str]] = mapped_column(String(3), default="EUR")
    price_per_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    
    area_useful_m2: Mapped[Optional[float]] = mapped_column(Float)
    area_gross_m2: Mapped[Optional[float]] = mapped_column(Float)
    area_land_m2: Mapped[Optional[float]] = mapped_column(Float)

    district: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    county: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    parish: Mapped[Optional[str]] = mapped_column(String(100))
    full_address: Mapped[Optional[str]] = mapped_column(String(500))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    has_garage: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_elevator: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_balcony: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_air_conditioning: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_pool: Mapped[Optional[bool]] = mapped_column(Boolean)

    energy_certificate: Mapped[Optional[str]] = mapped_column(String(10), comment="A+, A, B, B-, C, D, E, F")
    construction_year: Mapped[Optional[int]] = mapped_column(Integer)

    advertiser: Mapped[Optional[str]] = mapped_column(String(255))
    contacts: Mapped[Optional[str]] = mapped_column(String(500))

    raw_description: Mapped[Optional[str]] = mapped_column(Text, comment="Original unmodified description")
    description: Mapped[Optional[str]] = mapped_column(Text, comment="Cleaned description")
    enriched_description: Mapped[Optional[str]] = mapped_column(Text, comment="AI-enriched description")
    description_quality_score: Mapped[Optional[int]] = mapped_column(Integer, comment="0-100 quality score")
    meta_description: Mapped[Optional[str]] = mapped_column(Text)

    page_title: Mapped[Optional[str]] = mapped_column(String(500))
    headers: Mapped[Optional[dict]] = mapped_column(JSON, comment="Structured headers as JSON array")

    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, comment="Complete original payload")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    scrape_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), index=True)


    search_vector: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        nullable=True,
        comment="Full-text search tsvector — managed by DB trigger",
    )

    media_assets: Mapped[List["MediaAsset"]] = relationship(back_populates="listing", cascade="all, delete-orphan", lazy="selectin")
    price_history: Mapped[List["PriceHistory"]] = relationship(back_populates="listing", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        Index("ix_listings_property_type", "property_type"),
        Index("ix_listings_typology", "typology"),
        Index("ix_listings_price_amount", "price_amount"),
        Index("ix_listings_area_useful_m2", "area_useful_m2"),
        Index("ix_listings_source_partner_partner_id", "source_partner", "partner_id"),
        Index("ix_listings_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Listing(id={self.id}, title='{self.title}', source={self.source_partner})>"
