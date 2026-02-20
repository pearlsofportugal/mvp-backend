"""ScrapeJob SQLAlchemy model — tracks scraping job status and progress."""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_key: Mapped[str] = mapped_column(String(50), index=True, comment="pearls")
    base_url: Mapped[Optional[str]] = mapped_column(String(2048))
    start_url: Mapped[str] = mapped_column(String(2048))
    max_pages: Mapped[int] = mapped_column(Integer, default=10)
    
    # Status tracking
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        index=True,
        comment="pending, running, completed, failed, cancelled",
    )
    progress: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=dict,
        comment='{"pages_visited": 0, "listings_found": 0, "listings_scraped": 0, "errors": 0}',
    )
    config: Mapped[Optional[dict]] = mapped_column(
        JSON,
        comment="Runtime config: min_delay, max_delay, user_agent, etc.",
    )
    logs: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=dict,
        comment='{"errors": [], "warnings": [], "info": []}',
    )
    urls: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=dict,
        comment='{"found": [], "scraped": [], "failed": []}',
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamps
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<ScrapeJob(id={self.id}, site={self.site_key}, status={self.status})>"

    def mark_running(self) -> None:
        self.status = "running"
        self.started_at = datetime.now(timezone.utc)
        self.progress = {
            "pages_visited": 0,
            "listings_found": 0,
            "listings_scraped": 0,
            "errors": 0,
        }
        self.logs = {
            "errors": [],
            "warnings": [],
            "info": [],
        }
        self.urls = {
            "found": [],
            "scraped": [],
            "failed": [],
        }

    def mark_completed(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = datetime.now(timezone.utc)
        self.error_message = error

    def mark_cancelled(self) -> None:
        self.status = "cancelled"
        self.completed_at = datetime.now(timezone.utc)

    def update_progress(self, **kwargs: Any) -> None:
        """Update progress counters. E.g. update_progress(pages_visited=3, listings_scraped=15)."""
        if self.progress is None:
            self.progress = {}
        updated = {**self.progress, **kwargs}
        self.progress = updated

    def add_log(self, level: str, message: str, url: Optional[str] = None) -> None:
        """Add a log entry. Level: 'error', 'warning', 'info'."""
        if self.logs is None:
            self.logs = {"errors": [], "warnings": [], "info": []}
        
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        if url:
            log_entry["url"] = url
        
        if level in self.logs:
            self.logs[level].append(log_entry)
        else:
            self.logs[level] = [log_entry]

    def add_url(self, status: str, url: str) -> None:
        """Track URLs. Status: 'found', 'scraped', 'failed'."""
        if self.urls is None:
            self.urls = {"found": [], "scraped": [], "failed": []}
        
        if status in self.urls:
            if url not in self.urls[status]:
                self.urls[status].append(url)
        else:
            self.urls[status] = [url]
