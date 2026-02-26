"""MediaAsset SQLAlchemy model — images, floorplans, videos linked to listings."""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.listing_model import Listing


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        index=True,
    )
    url: Mapped[str] = mapped_column(String(2048))
    alt_text: Mapped[Optional[str]] = mapped_column(String(500))
    type: Mapped[Optional[str]] = mapped_column(String(20), comment="photo, floorplan, video")
    position: Mapped[Optional[int]] = mapped_column(Integer, comment="Display order")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing: Mapped["Listing"] = relationship(back_populates="media_assets")

    def __repr__(self) -> str:
        return f"<MediaAsset(id={self.id}, type='{self.type}', url='{self.url[:60]}...')>"
