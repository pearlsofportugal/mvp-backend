"""Cloud Scheduler service — manages per-site cron jobs via Google Cloud Scheduler API.

Replaces APScheduler for production deployments. Jobs live outside the process,
so they survive Cloud Run instance restarts and Cold Starts without re-registration.

Schedule → cron conversion:
  interval_minutes < 60       → */N * * * *        (e.g. 30 → */30 * * * *)
  interval_minutes == 60      → 0 * * * *
  interval_minutes % 60 == 0  → 0 */H * * *        (e.g. 120 → 0 */2 * * *)
  interval_minutes >= 1440    → M H * * *  anchored to start_at wall-clock time
  other                       → best-effort hourly approximation

When GOOGLE_CLOUD_PROJECT is not set (local dev), all methods are no-ops.
"""
from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME_PREFIX = "scrape-"


def _job_resource_name(site_key: str, project: str, location: str) -> str:
    return f"projects/{project}/locations/{location}/jobs/{_JOB_NAME_PREFIX}{site_key}"


def _to_cron(interval_minutes: int, start_at: datetime | None) -> str:
    """Convert interval_minutes + optional start_at to a cron expression."""
    h = start_at.hour if start_at else 0
    m = start_at.minute if start_at else 0

    if interval_minutes >= 1440:
        # Daily — fire once at the configured wall-clock time
        return f"{m} {h} * * *"
    if interval_minutes == 60:
        return "0 * * * *"
    if interval_minutes % 60 == 0:
        hours = interval_minutes // 60
        return f"0 */{hours} * * *"
    if interval_minutes < 60:
        return f"*/{interval_minutes} * * * *"
    # Irregular sub-day interval — round to nearest hour
    hours = max(1, round(interval_minutes / 60))
    return f"0 */{hours} * * *"


class CloudSchedulerService:
    """Manages Google Cloud Scheduler HTTP jobs for per-site scraping.

    All public methods are synchronous — they use the sync GCP client.
    No-op when GOOGLE_CLOUD_PROJECT is not configured (local/test env).
    """

    # ------------------------------------------------------------------
    # Public interface (mirrors APScheduler SchedulerService)
    # ------------------------------------------------------------------

    def schedule_site(self, site) -> None:  # type: ignore[no-untyped-def]
        """Create or update the Cloud Scheduler job for a site."""
        from app.config import settings

        if not settings.google_cloud_project:
            logger.debug("GOOGLE_CLOUD_PROJECT not set — skipping Cloud Scheduler sync for '%s'", site.key)
            return

        if not site.schedule_enabled or not site.schedule_interval_minutes:
            self.unschedule_site(site.key)
            return

        try:
            self._upsert_job(site)
        except Exception as exc:
            logger.error(
                "Failed to sync Cloud Scheduler job for site '%s': %s", site.key, exc
            )

    def unschedule_site(self, site_key: str) -> None:
        """Delete the Cloud Scheduler job for a site. Silent if not found."""
        from app.config import settings

        if not settings.google_cloud_project:
            return

        try:
            self._delete_job(site_key)
        except Exception as exc:
            if "NOT_FOUND" not in str(exc) and "404" not in str(exc):
                logger.error(
                    "Failed to delete Cloud Scheduler job for '%s': %s", site_key, exc
                )

    def get_next_run(self, site_key: str) -> datetime | None:
        """Return the next scheduled run time from Cloud Scheduler, or None."""
        from app.config import settings

        if not settings.google_cloud_project:
            return None

        try:
            return self._fetch_next_run(site_key)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal — Google Cloud Scheduler API calls (sync client)
    # ------------------------------------------------------------------

    def _client(self):
        from google.cloud import scheduler_v1  # type: ignore[import]
        return scheduler_v1.CloudSchedulerClient()

    def _upsert_job(self, site) -> None:  # type: ignore[no-untyped-def]
        from google.cloud import scheduler_v1  # type: ignore[import]
        from app.config import settings

        project = settings.google_cloud_project
        location = settings.google_cloud_scheduler_location
        backend_url = settings.backend_url.rstrip("/")

        client = self._client()
        parent = f"projects/{project}/locations/{location}"
        job_name = _job_resource_name(site.key, project, location)

        cron = _to_cron(
            site.schedule_interval_minutes,
            site.schedule_start_at,
        )

        job = scheduler_v1.Job(
            name=job_name,
            http_target=scheduler_v1.HttpTarget(
                uri=f"{backend_url}/api/v1/jobs/trigger/{site.key}",
                http_method=scheduler_v1.HttpMethod.POST,
                headers={
                    "X-API-Key": settings.api_key,
                    "Content-Type": "application/json",
                },
            ),
            schedule=cron,
            time_zone=site.schedule_timezone,
            # Cloud Scheduler will retry on 5xx — set deadline to 30 min
            attempt_deadline={"seconds": 1800},
        )

        try:
            client.update_job(request=scheduler_v1.UpdateJobRequest(job=job))
            logger.info(
                "Updated Cloud Scheduler job for site '%s' — cron: %s", site.key, cron
            )
        except Exception as exc:
            if "NOT_FOUND" in str(exc) or "404" in str(exc):
                client.create_job(
                    request=scheduler_v1.CreateJobRequest(parent=parent, job=job)
                )
                logger.info(
                    "Created Cloud Scheduler job for site '%s' — cron: %s", site.key, cron
                )
            else:
                raise

    def _delete_job(self, site_key: str) -> None:
        from google.cloud import scheduler_v1  # type: ignore[import]
        from app.config import settings

        project = settings.google_cloud_project
        location = settings.google_cloud_scheduler_location
        client = self._client()
        job_name = _job_resource_name(site_key, project, location)
        client.delete_job(request=scheduler_v1.DeleteJobRequest(name=job_name))
        logger.info("Deleted Cloud Scheduler job for site '%s'", site_key)

    def _fetch_next_run(self, site_key: str) -> datetime | None:
        from google.cloud import scheduler_v1  # type: ignore[import]
        from app.config import settings

        project = settings.google_cloud_project
        location = settings.google_cloud_scheduler_location
        client = self._client()
        job_name = _job_resource_name(site_key, project, location)
        job = client.get_job(request=scheduler_v1.GetJobRequest(name=job_name))
        # schedule_time is a google.protobuf.Timestamp → auto-converted to datetime by the SDK
        return job.schedule_time or None  # type: ignore[return-value]
    def schedule_imodigi_sync(
    self,
    *,
    interval_minutes: int = 60,
    limit: int = 50,
) -> None:
        """Cria ou actualiza o Cloud Scheduler job para o sync Imodigi."""
        from app.config import settings
    
        if not settings.google_cloud_project:
            logger.debug(
                "GOOGLE_CLOUD_PROJECT não definido — a ignorar Cloud Scheduler para Imodigi sync"
            )
            return
    
        try:
            self._upsert_imodigi_job(interval_minutes=interval_minutes, limit=limit)
        except Exception as exc:
            logger.error("Falha ao registar Cloud Scheduler job para Imodigi sync: %s", exc)


    def unschedule_imodigi_sync(self) -> None:
        """Remove o Cloud Scheduler job do Imodigi sync."""
        from app.config import settings

        if not settings.google_cloud_project:
            return

        try:
            self._delete_imodigi_job()
        except Exception as exc:
            if "NOT_FOUND" not in str(exc) and "404" not in str(exc):
                logger.error("Falha ao remover Cloud Scheduler job Imodigi: %s", exc)


    def _upsert_imodigi_job(
        self,
        *,
        interval_minutes: int,
        limit: int,
    ) -> None:
        from google.cloud import scheduler_v1
        from app.config import settings

        project = settings.google_cloud_project
        location = settings.google_cloud_scheduler_location
        backend_url = settings.backend_url.rstrip("/")

        client = self._client()
        parent = f"projects/{project}/locations/{location}"
        job_name = f"{parent}/jobs/imodigi-sync"
        cron = _to_cron(interval_minutes, None)

        job = scheduler_v1.Job(
            name=job_name,
            http_target=scheduler_v1.HttpTarget(
                uri=f"{backend_url}/api/v1/imodigi/trigger/sync?limit={limit}",
                http_method=scheduler_v1.HttpMethod.POST,
                headers={
                    "X-API-Key": settings.api_key,
                    "Content-Type": "application/json",
                },
            ),
            schedule=cron,
            time_zone="Europe/Lisbon",
            attempt_deadline={"seconds": 1800},
        )

        try:
            client.update_job(request=scheduler_v1.UpdateJobRequest(job=job))
            logger.info("Cloud Scheduler job Imodigi sync actualizado — cron: %s", cron)
        except Exception as exc:
            if "NOT_FOUND" in str(exc) or "404" in str(exc):
                client.create_job(
                    request=scheduler_v1.CreateJobRequest(parent=parent, job=job)
                )
                logger.info("Cloud Scheduler job Imodigi sync criado — cron: %s", cron)
            else:
                raise


    def _delete_imodigi_job(self) -> None:
        from google.cloud import scheduler_v1
        from app.config import settings

        project = settings.google_cloud_project
        location = settings.google_cloud_scheduler_location
        client = self._client()
        job_name = f"projects/{project}/locations/{location}/jobs/imodigi-sync"
        client.delete_job(request=scheduler_v1.DeleteJobRequest(name=job_name))
        logger.info("Cloud Scheduler job Imodigi sync removido")

# Singleton — imported by scheduler_service.py
cloud_scheduler_service = CloudSchedulerService()
