"""SiteConfig SQLAlchemy model — site scraping configuration stored in DB."""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SiteConfig(Base):
    __tablename__ = "site_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(2048))
    selectors: Mapped[dict] = mapped_column(JSON, default=dict)
    extraction_mode: Mapped[str] = mapped_column(String(20), default="direct")

    pagination_type: Mapped[str] = mapped_column(String(20), nullable=False, default="html_next")
    pagination_param: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    link_pattern: Mapped[Optional[str]] = mapped_column(String(500))
    image_filter: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<SiteConfig(key='{self.key}', name='{self.name}', active={self.is_active})>"
