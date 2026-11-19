"""AiUsageLog — one row per Gemini call, so spend can be seen and capped.

Written by the Gemini adapter (every caller is covered: SEO enrichment, the generic-ingest
fallback, ...). ``listing_id`` is deliberately not a foreign key: usage history must outlive a
deleted listing, and the adapter writes it from outside the request's DB session.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

STATUS_OK = "ok"
STATUS_ERROR = "error"


class AiUsageLog(Base):
    __tablename__ = "ai_usage_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    operation: Mapped[str] = mapped_column(String(40), default="unknown", comment="seo_copywriting, listing_extract, ...")
    model: Mapped[str] = mapped_column(String(80))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    source_partner: Mapped[str | None] = mapped_column(String(50), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1, comment="API attempts made (retries can each be billed)")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer, comment="candidate + thinking tokens")
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), comment="NULL when no prices are configured")
    status: Mapped[str] = mapped_column(String(10), default=STATUS_OK)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<AiUsageLog(op={self.operation}, status={self.status}, tokens={self.total_tokens})>"
