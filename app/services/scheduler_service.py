"""Scheduler service — manages per-site cron scraping via APScheduler.

Uses APScheduler 3.x AsyncIOScheduler with an in-memory job store.
Scheduling configuration (interval, timezone, start URL, etc.) is persisted
in the SiteConfig DB fields; APScheduler only manages the in-process timers.

On startup the lifespan hook loads all sites with schedule_enabled=True and
registers them. On PATCH the router triggers reschedule/unschedule as needed.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.exceptions import JobAlreadyRunningError
from app.core.logging import get_logger
from app.database import async_session_factory
from app.models.site_config_model import SiteConfig

logger = get_logger(__name__)

_JOB_ID_PREFIX = "scrape__"


def _job_id(site_key: str) -> str:
    return f"{_JOB_ID_PREFIX}{site_key}"


def _localize_start_date(start_date: datetime | None, tz: ZoneInfo) -> datetime | None:
    """Return start_date converted to the site's timezone.

    Two cases from the DB:
    - Naive datetime (no tzinfo): frontend sent wall-clock time without offset
      → attach target timezone directly (preserve wall-clock value).
    - Aware datetime (UTC from PostgreSQL): frontend sent a tz-aware value
      (e.g. 12:35+01:00 Lisbon) which PostgreSQL stored as UTC (11:35+00:00)
      → convert properly with astimezone() so 11:35 UTC → 12:35 Lisbon.

    Example: user intends 12:35 Lisbon → frontend sends 12:35+01:00
             → DB stores 11:35+00:00 UTC → astimezone(Lisbon) → 12:35+01:00 ✓
    """
    if start_date is None:
        return None
    if start_date.tzinfo is None:
        # Naive — treat wall-clock value as local time directly
        return start_date.replace(tzinfo=tz)
    # Aware (UTC or other) — proper timezone conversion
    return start_date.astimezone(tz)


def _build_trigger(
    interval_minutes: int, start: datetime | None, tz: ZoneInfo
) -> CronTrigger | IntervalTrigger:
    """Build the appropriate APScheduler trigger.

    - Daily (>= 1440 min): CronTrigger at H:M — fires once per day at the
      exact configured time. CronTrigger is correct here because it pins to
      a wall-clock time regardless of DST changes.

    - Sub-daily (< 1440 min): IntervalTrigger anchored to today's H:M.
      APScheduler auto-advances a past start_date to the next future occurrence:
        next_fire = start_date + ceil((now - start_date) / interval) * interval
      This correctly wraps around midnight and avoids the two bugs that
      CronTrigger has for sub-daily intervals:
        1. `minute="{m}/{step}"` only fires once/hour when m + step > 59.
        2. `hour="{h}/{step_h}"` does not wrap past midnight — creates large gaps.
    """
    if start is not None:
        h, m = start.hour, start.minute
    else:
        now = datetime.now(tz)
        h, m = now.hour, now.minute

    if interval_minutes >= 1440:
        # Daily — CronTrigger pinned to H:M, fires every day at that time.
        return CronTrigger(hour=h, minute=m, timezone=tz)

    # Sub-daily — IntervalTrigger anchored to the configured H:M today.
    # Using today's date is fine: if start_dt is in the past, APScheduler
    # advances it forward by the interval automatically.
    today = datetime.now(tz)
    start_dt = today.replace(hour=h, minute=m, second=0, microsecond=0)
    return IntervalTrigger(minutes=interval_minutes, start_date=start_dt, timezone=tz)


async def _run_scheduled_scrape(site_key: str) -> None:
    """Entry point invoked by APScheduler for each scheduled site.

    Opens its own DB session (same pattern as run_scrape_job), validates the
    site is still active and scheduling still enabled, then creates a ScrapeJob
    and hands off to the scraper background task.

    JobAlreadyRunningError is silenced — it simply means a previous run is
    still in progress and the new one is skipped.
    """
    from app.repositories.site_config_repository import SiteConfigRepository
    from app.schemas.scrape_job_schema import JobCreate
    from app.services.scrape_job_service import ScrapeJobService
    from app.services.scraper_service import run_scrape_job

    logger.info("Scheduled scrape triggered for site '%s'", site_key)

    async with async_session_factory() as db:
        site = await SiteConfigRepository.get_by_key(db, site_key)
        if not site or not site.is_active or not site.schedule_enabled:
            logger.warning(
                "Scheduled scrape skipped for site '%s' — site inactive or scheduling disabled",
                site_key,
            )
            return

        start_url = site.schedule_start_url or site.base_url
        max_pages = site.schedule_max_pages or settings.default_max_pages

        payload = JobCreate(site_key=site_key, start_url=start_url, max_pages=max_pages)

        try:
            job = await ScrapeJobService.create_job(db, payload)
        except JobAlreadyRunningError:
            logger.warning(
                "Scheduled scrape skipped for site '%s' — another job is already running",
                site_key,
            )
            return
        except Exception:
            logger.exception("Scheduled scrape failed to create job for site '%s'", site_key)
            return

    # run_scrape_job opens its own session — must be called after the creation
    # session above is closed (async with block exited).
    await run_scrape_job(str(job.id))


class SchedulerService:
    """Thin wrapper around APScheduler's AsyncIOScheduler for site-based cron jobs."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, sites: list[SiteConfig]) -> None:
        """Start the scheduler and register all provided scheduled sites."""
        for site in sites:
            self._register(site)
        self._scheduler.start()
        logger.info(
            "Scheduler started with %d site(s) registered",
            len(sites),
        )

    def shutdown(self) -> None:
        """Stop the scheduler gracefully (does not wait for running jobs)."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ------------------------------------------------------------------
    # Per-site management
    # ------------------------------------------------------------------

    def schedule_site(self, site: SiteConfig) -> None:
        """Register or update the scheduled job for a site.

        If schedule_enabled is False the job is removed (if present).
        Always safe to call — idempotent via replace_existing=True.
        """
        job_id = _job_id(site.key)

        if not site.schedule_enabled or not site.schedule_interval_minutes:
            self.unschedule_site(site.key)
            return

        tz = ZoneInfo(site.schedule_timezone)
        start = _localize_start_date(site.schedule_start_at, tz)
        trigger = _build_trigger(site.schedule_interval_minutes, start, tz)

        self._scheduler.add_job(
            _run_scheduled_scrape,
            trigger=trigger,
            args=[site.key],
            id=job_id,
            replace_existing=True,
            name=f"scrape:{site.key}",
        )
        next_run = self.get_next_run(site.key)
        logger.info(
            "Scheduled scrape registered for site '%s' — every %d min, next run: %s",
            site.key,
            site.schedule_interval_minutes,
            next_run.isoformat() if next_run else "unknown",
        )

    def unschedule_site(self, site_key: str) -> None:
        """Remove the scheduled job for a site. Silent if not registered."""
        job_id = _job_id(site_key)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
            logger.info("Scheduled scrape removed for site '%s'", site_key)

    def get_next_run(self, site_key: str) -> datetime | None:
        """Return the next scheduled run time, or None if not registered."""
        job = self._scheduler.get_job(_job_id(site_key))
        if job is None:
            return None
        return job.next_run_time  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _register(self, site: SiteConfig) -> None:
        """Register a site job without logging — used during bulk startup."""
        if not site.schedule_enabled or not site.schedule_interval_minutes:
            return
        tz = ZoneInfo(site.schedule_timezone)
        start = _localize_start_date(site.schedule_start_at, tz)
        trigger = _build_trigger(site.schedule_interval_minutes, start, tz)
        self._scheduler.add_job(
            _run_scheduled_scrape,
            trigger=trigger,
            args=[site.key],
            id=_job_id(site.key),
            replace_existing=True,
            name=f"scrape:{site.key}",
        )


# Singleton — imported by main.py and sites.py
scheduler_service = SchedulerService()
