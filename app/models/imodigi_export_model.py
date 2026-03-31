"""ImodigiExport SQLAlchemy model — tracks listings published to the Imodigi CRM.

Each row represents a 1-to-1 relationship between a local Listing and a published
property in the Imodigi CRM (unique per listing_id).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ImodigiExport(Base):
    __tablename__ = "imodigi_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Relationship to local listing
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Imodigi-side identifiers (populated after first successful POST)
    imodigi_property_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="ID returned by POST /crm-properties.php")
    imodigi_reference: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="Platform reference, e.g. PG09041")
    imodigi_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="ID da loja usada no momento do export")

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
        comment="pending | published | updated | failed",
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Audit timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
