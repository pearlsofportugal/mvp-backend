"""SoldListingUrl SQLAlchemy model — caches URLs confirmed sold/reserved.

Some partner sitemaps never remove sold/reserved listings, so every scrape
re-pays the full fetch cost (JS render, ethical delay) just to rediscover
the same dead URL. This table lets the scraper skip a URL outright once it
was recently confirmed sold, instead of re-fetching it every run.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SoldListingUrl(Base):
    __tablename__ = "sold_listing_urls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_partner: Mapped[str] = mapped_column(String(50), index=True)
    source_url: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<SoldListingUrl(partner={self.source_partner}, url={self.source_url})>"
