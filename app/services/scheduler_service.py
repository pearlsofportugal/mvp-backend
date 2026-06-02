"""Scheduler service - thin facade over CloudSchedulerService.

Public interface is preserved so sites.py and main.py need no changes.
All actual scheduling is delegated to cloud_scheduler_service, which manages
Google Cloud Scheduler jobs outside the process (survives restarts/scaling).

For local development (GOOGLE_CLOUD_PROJECT not set), all calls are no-ops.
"""
from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.models.site_config_model import SiteConfig
from app.services.cloud_scheduler_service import cloud_scheduler_service

logger = get_logger(__name__)


class SchedulerService:
    """Thin facade over CloudSchedulerService.

    Public interface is identical to the old APScheduler-based version so
    sites.py and main.py require no changes.
    start() and shutdown() are no-ops - Cloud Scheduler is external/persistent.
    """

    def start(self, sites: list[SiteConfig]) -> None:
        """No-op - Cloud Scheduler jobs are persistent, no re-registration needed."""
        logger.info(
            "Scheduler: using Google Cloud Scheduler (external) - %d site(s) configured",
            len(sites),
        )

    def shutdown(self, wait: bool = True) -> None:
        """No-op - Cloud Scheduler is external, nothing to stop."""
        logger.info("Scheduler: shutdown called (no-op for Cloud Scheduler)")

    def schedule_site(self, site: SiteConfig) -> None:
        """Create or update the Cloud Scheduler job for a site."""
        cloud_scheduler_service.schedule_site(site)
        next_run = self.get_next_run(site.key)
        logger.info(
            "Cloud Scheduler job synced for site '%s' - next run: %s",
            site.key,
            next_run.isoformat() if next_run else "unknown",
        )

    def unschedule_site(self, site_key: str) -> None:
        """Remove the Cloud Scheduler job for a site."""
        cloud_scheduler_service.unschedule_site(site_key)

    def get_next_run(self, site_key: str) -> datetime | None:
        """Return the next scheduled run time from Cloud Scheduler."""
        return cloud_scheduler_service.get_next_run(site_key)


# Singleton - imported by main.py and sites.py
scheduler_service = SchedulerService()