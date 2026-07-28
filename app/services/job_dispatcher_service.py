"""Dispatch persisted scrape jobs to a local task or Google Cloud Tasks."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.config import settings
from app.core.logging import get_logger
from app.services.scrape_job_event_service import record_event

if TYPE_CHECKING:
    from fastapi import BackgroundTasks
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.scrape_job_model import ScrapeJob

logger = get_logger(__name__)


async def dispatch_scrape_job(
    db: "AsyncSession", job: "ScrapeJob", background_tasks: "BackgroundTasks | None" = None
) -> None:
    """Dispatch after the job has been committed.

    ``local`` preserves the development workflow. Production uses Cloud Tasks to
    deliver a short authenticated request to an internal dispatcher; that
    dispatcher starts the isolated Cloud Run Job execution.
    """
    if job.dispatched_at is not None or job.status != "pending":
        return

    if settings.job_dispatch_mode == "local":
        if background_tasks is None:
            from app.services.scraper_service import run_scrape_job
            asyncio.create_task(run_scrape_job(str(job.id)))
        else:
            from app.services.scraper_service import run_scrape_job
            background_tasks.add_task(run_scrape_job, str(job.id))
        job.dispatched_at = datetime.now(timezone.utc)
        job.dispatch_attempts += 1
        await record_event(db, job.id, "dispatched", "Job scheduled for local worker")
        await db.commit()
        return

    await _enqueue_cloud_task(job.id)
    job.dispatched_at = datetime.now(timezone.utc)
    job.dispatch_attempts += 1
    await record_event(db, job.id, "dispatched", "Job enqueued in Cloud Tasks")
    await db.commit()


async def _enqueue_cloud_task(job_id: UUID) -> None:
    required = {
        "CLOUD_TASKS_PROJECT": settings.cloud_tasks_project,
        "WORKER_DISPATCH_URL": settings.worker_dispatch_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Cloud Tasks dispatch is not configured: {', '.join(missing)}")

    try:
        from google.cloud import tasks_v2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Install google-cloud-tasks to use JOB_DISPATCH_MODE=cloud_tasks") from exc

    def enqueue() -> None:
        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(
            settings.cloud_tasks_project, settings.cloud_tasks_location, settings.cloud_tasks_queue
        )
        headers = {"Content-Type": "application/json"}
        if settings.worker_dispatch_token:
            headers["X-Worker-Dispatch-Token"] = settings.worker_dispatch_token
        request: dict = {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": settings.worker_dispatch_url.rstrip("/") + "/internal/workers/scrape",
            "headers": headers,
            "body": base64.b64encode(json.dumps({"job_id": str(job_id)}).encode()).decode(),
        }
        if settings.worker_dispatch_service_account:
            request["oidc_token"] = {"service_account_email": settings.worker_dispatch_service_account}
        client.create_task(request={"parent": parent, "task": {"http_request": request}})

    await asyncio.to_thread(enqueue)


async def start_cloud_run_scrape_worker(job_id: UUID) -> str:
    """Start one Cloud Run Job execution for a persisted scrape job."""
    if not settings.cloud_run_scrape_job_name or not settings.google_cloud_project:
        raise RuntimeError("CLOUD_RUN_SCRAPE_JOB_NAME and GOOGLE_CLOUD_PROJECT are required")
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as exc:
        raise RuntimeError("Google authentication libraries are required to start Cloud Run Jobs") from exc

    def run() -> str:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        session = AuthorizedSession(credentials)
        url = (
            "https://run.googleapis.com/v2/projects/"
            f"{settings.google_cloud_project}/locations/{settings.cloud_tasks_location}/jobs/"
            f"{settings.cloud_run_scrape_job_name}:run"
        )
        response = session.post(
            url,
            json={"overrides": {"containerOverrides": [{"args": ["--scrape-job-id", str(job_id)]}]}},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("name", "")

    return await asyncio.to_thread(run)
