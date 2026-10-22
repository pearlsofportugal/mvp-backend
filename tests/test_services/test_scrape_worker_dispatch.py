"""Scrapes run in a Cloud Run Job: dispatch through Cloud Tasks, start the Job, free dead jobs."""
import asyncio
import base64
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import internal
from app.config import settings
from app.core.exceptions import JobAlreadyRunningError
from app.models.scrape_job_model import ScrapeJob
from app.repositories.scrape_job_repository import ScrapeJobRepository
from app.schemas.scrape_job_schema import JobCreate
from app.services import job_dispatcher_service as dispatcher
from app.services.scrape_job_service import ScrapeJobService
from tests.conftest import make_site_config_payload


def _job(**kw) -> ScrapeJob:
    base = dict(
        id=uuid4(), site_key="t2mais1", start_url="https://www.t2mais1.pt/imoveis", max_pages=10,
        status="pending", dispatched_at=None, dispatch_attempts=0, attempt_count=0,
    )
    base.update(kw)
    return ScrapeJob(**base)


# ── Cloud Tasks dispatch ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def cloud_tasks(monkeypatch):
    monkeypatch.setattr(settings, "job_dispatch_mode", "cloud_tasks")
    monkeypatch.setattr(settings, "cloud_tasks_project", "proj")
    monkeypatch.setattr(settings, "cloud_tasks_location", "europe-west1")
    monkeypatch.setattr(settings, "cloud_tasks_queue", "scrape-jobs")
    monkeypatch.setattr(settings, "worker_dispatch_url", "https://api.example.run.app/")
    monkeypatch.setattr(settings, "worker_dispatch_token", "s3cret")
    monkeypatch.setattr(settings, "worker_dispatch_service_account", "invoker@proj.iam.gserviceaccount.com")


def _fake_tasks_v2():
    client = MagicMock()
    client.queue_path.return_value = "projects/proj/locations/europe-west1/queues/scrape-jobs"
    module = types.SimpleNamespace(
        CloudTasksClient=MagicMock(return_value=client),
        HttpMethod=types.SimpleNamespace(POST="POST"),
    )
    return module, client


def test_cloud_tasks_mode_enqueues_a_task_for_the_internal_worker_endpoint(cloud_tasks):
    module, client = _fake_tasks_v2()
    job = _job()
    db = MagicMock()
    db.commit = AsyncMock()
    fake_cloud = types.SimpleNamespace(tasks_v2=module)  # `from google.cloud import tasks_v2`
    with patch.dict(sys.modules, {"google.cloud": fake_cloud, "google.cloud.tasks_v2": module}),          patch.object(dispatcher, "record_event", new=AsyncMock()) as record:
        asyncio.run(dispatcher.dispatch_scrape_job(db, job))

    task = client.create_task.call_args.kwargs["request"]["task"]["http_request"]
    assert task["url"] == "https://api.example.run.app/internal/workers/scrape"
    assert task["headers"]["X-Worker-Dispatch-Token"] == "s3cret"
    assert json.loads(base64.b64decode(task["body"])) == {"job_id": str(job.id)}
    assert task["oidc_token"] == {"service_account_email": "invoker@proj.iam.gserviceaccount.com"}
    assert job.dispatched_at is not None and job.dispatch_attempts == 1
    assert record.await_args.args[2] == "dispatched"


def test_dispatch_is_skipped_when_the_job_was_already_dispatched(cloud_tasks):
    job = _job(dispatched_at=datetime.now(timezone.utc))
    with patch.object(dispatcher, "_enqueue_cloud_task", new=AsyncMock()) as enqueue:
        asyncio.run(dispatcher.dispatch_scrape_job(MagicMock(), job))
    enqueue.assert_not_awaited()


def test_cloud_tasks_mode_without_configuration_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "cloud_tasks_project", "")
    monkeypatch.setattr(settings, "worker_dispatch_url", "")
    with pytest.raises(RuntimeError, match="CLOUD_TASKS_PROJECT"):
        asyncio.run(dispatcher._enqueue_cloud_task(uuid4()))


# ── Starting the Cloud Run Job ──────────────────────────────────────────────────────────────────────

def test_cloud_run_job_is_started_with_the_scrape_job_id_as_argument(monkeypatch):
    monkeypatch.setattr(settings, "cloud_run_scrape_job_name", "mvp-scrape-worker")
    monkeypatch.setattr(settings, "google_cloud_project", "proj")
    monkeypatch.setattr(settings, "cloud_tasks_location", "europe-west1")
    session = MagicMock()
    session.post.return_value.json.return_value = {"name": "operations/abc"}
    job_id = uuid4()
    with patch("google.auth.default", return_value=(MagicMock(), "proj")), \
         patch("google.auth.transport.requests.AuthorizedSession", return_value=session):
        result = asyncio.run(dispatcher.start_cloud_run_scrape_worker(job_id))

    assert result == "operations/abc"
    url = session.post.call_args.args[0]
    assert url == "https://run.googleapis.com/v2/projects/proj/locations/europe-west1/jobs/mvp-scrape-worker:run"
    assert session.post.call_args.kwargs["json"] == {
        "overrides": {"containerOverrides": [{"args": ["--scrape-job-id", str(job_id)]}]}
    }


def test_starting_the_job_requires_its_name_and_project(monkeypatch):
    monkeypatch.setattr(settings, "cloud_run_scrape_job_name", "")
    with pytest.raises(RuntimeError, match="CLOUD_RUN_SCRAPE_JOB_NAME"):
        asyncio.run(dispatcher.start_cloud_run_scrape_worker(uuid4()))


# ── Internal endpoint called by the task ────────────────────────────────────────────────────────────

def test_internal_endpoint_rejects_a_wrong_or_missing_token(monkeypatch):
    monkeypatch.setattr(settings, "worker_dispatch_token", "s3cret")
    for bad in (None, "", "nope"):
        with pytest.raises(HTTPException) as exc:
            internal._verify_dispatch_token(bad)
        assert exc.value.status_code == 401
    internal._verify_dispatch_token("s3cret")  # no exception


def test_internal_endpoint_refuses_every_token_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "worker_dispatch_token", "")
    with pytest.raises(HTTPException):
        internal._verify_dispatch_token("")


def test_internal_endpoint_starts_the_worker_once_and_is_idempotent():
    job = _job()
    db = MagicMock()
    db.get = AsyncMock(return_value=job)
    db.commit = AsyncMock()
    payload = internal.WorkerDispatchRequest(job_id=job.id)
    with patch.object(internal, "start_cloud_run_scrape_worker", new=AsyncMock(return_value="exec-1")) as start, \
         patch.object(internal, "record_event", new=AsyncMock()):
        first = asyncio.run(internal.start_scrape_worker(payload, db))
        job.status = "running"  # the worker picked it up; a retried task must not start a second execution
        second = asyncio.run(internal.start_scrape_worker(payload, db))

    assert first == {"job_id": str(job.id), "execution_id": "exec-1"}
    assert job.execution_id == "exec-1"
    assert second == {"job_id": str(job.id), "status": "running"}
    start.assert_awaited_once()


def test_internal_endpoint_404_for_an_unknown_job():
    db = MagicMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(internal.start_scrape_worker(internal.WorkerDispatchRequest(job_id=uuid4()), db))
    assert exc.value.status_code == 404


def test_worker_entrypoint_runs_the_job_it_is_given(monkeypatch):
    from app import worker

    ran = []

    async def fake_run(job_id):
        ran.append(job_id)

    monkeypatch.setattr(worker, "run_scrape_job", fake_run)
    monkeypatch.setattr(sys, "argv", ["app.worker", "--scrape-job-id", "abc-123"])
    worker.main()
    assert ran == ["abc-123"]


# ── Dead jobs must not block the site forever ───────────────────────────────────────────────────────

async def _site(db_session, key="t2mais1"):
    from app.repositories.site_config_repository import SiteConfigRepository  # noqa: F401
    from app.models.site_config_model import SiteConfig

    payload = make_site_config_payload(key=key, name=key.title(), base_url="https://www.t2mais1.pt")
    site = SiteConfig(**{k: v for k, v in payload.items() if hasattr(SiteConfig, k)})
    db_session.add(site)
    await db_session.commit()
    return site


async def test_a_running_job_without_heartbeat_is_freed_so_the_site_can_run_again(db_session):
    await _site(db_session)
    dead = _job(status="running", last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db_session.add(dead)
    await db_session.commit()

    job = await ScrapeJobService.create_job(
        db_session, JobCreate(site_key="t2mais1", start_url="https://www.t2mais1.pt/imoveis", max_pages=5)
    )

    await db_session.refresh(dead)
    assert dead.status == "failed" and "heartbeat" in dead.error_message
    assert job.status == "pending" and job.id != dead.id


async def test_a_healthy_running_job_still_blocks_a_second_one(db_session):
    await _site(db_session)
    db_session.add(_job(status="running", last_heartbeat_at=datetime.now(timezone.utc)))
    await db_session.commit()

    with pytest.raises(JobAlreadyRunningError):
        await ScrapeJobService.create_job(
            db_session, JobCreate(site_key="t2mais1", start_url="https://www.t2mais1.pt/imoveis", max_pages=5)
        )


async def test_a_job_pending_for_hours_is_abandoned_but_a_fresh_one_is_not(db_session):
    await _site(db_session)
    old = _job(created_at=datetime.now(timezone.utc) - timedelta(hours=3))
    db_session.add(old)
    await db_session.commit()
    now = datetime.now(timezone.utc)

    freed = await ScrapeJobRepository.fail_abandoned_jobs(
        db_session, "t2mais1",
        running_cutoff=now - timedelta(minutes=5), pending_cutoff=now - timedelta(hours=1),
    )
    assert freed == 1 and old.status == "failed"

    fresh = _job(created_at=now)
    db_session.add(fresh)
    await db_session.commit()
    assert await ScrapeJobRepository.fail_abandoned_jobs(
        db_session, "t2mais1",
        running_cutoff=now - timedelta(minutes=5), pending_cutoff=now - timedelta(hours=1),
    ) == 0
    assert fresh.status == "pending"
