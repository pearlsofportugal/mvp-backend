"""scripts/setup_scrape_worker.py: dry-run must show the right commands and never leak values."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_scrape_worker.py"
SPEC = importlib.util.spec_from_file_location("setup_scrape_worker", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

DB_PASSWORD = "hunter2-do-not-print"
DESCRIBE = {
    "status": {"url": "https://mvp-backend-abc.europe-west1.run.app"},
    "spec": {"template": {
        "metadata": {"annotations": {"run.googleapis.com/cloudsql-instances": "proj:europe-west1:db"}},
        "spec": {
            "serviceAccountName": "api@proj.iam.gserviceaccount.com",
            "containers": [{
                "image": "europe-west1-docker.pkg.dev/proj/repo/mvp-backend:abc123",
                "env": [
                    {"name": "DATABASE_URL", "value": f"postgresql://u:{DB_PASSWORD}@/db"},
                    {"name": "APP_ENV", "value": "production"},
                    {"name": "API_KEY", "valueFrom": {"secretKeyRef": {"name": "api-key", "key": "latest"}}},
                ],
            }],
        },
    }},
}
ARGS = SimpleNamespace(project="proj", region="europe-west1", service="mvp-backend", job="mvp-scrape-worker", queue="scrape-jobs")


def _dry_run(stage, capsys):
    g = mod.Gcloud(apply=False)
    with patch.object(mod.Gcloud, "read", return_value=json.dumps(DESCRIBE)), \
         patch.object(mod.Gcloud, "exists", return_value=False), \
         patch.object(mod.subprocess, "run", side_effect=AssertionError("dry-run must not execute commands")):
        stage(g, ARGS)
    return capsys.readouterr().out


def test_service_info_copies_plain_env_and_secret_references():
    with patch.object(mod.Gcloud, "read", return_value=json.dumps(DESCRIBE)):
        info = mod.service_info(mod.Gcloud(False), ARGS)
    assert info["sa"] == "api@proj.iam.gserviceaccount.com"
    assert info["sql"] == "proj:europe-west1:db"
    assert info["env"]["APP_ENV"] == "production"
    assert info["secrets"] == {"API_KEY": "api-key:latest"}
    assert info["url"].startswith("https://mvp-backend")


def test_infra_dry_run_prints_the_expected_commands(capsys):
    out = _dry_run(mod.stage_infra, capsys)
    assert "tasks queues create scrape-jobs" in out
    assert "tasks queues add-iam-policy-binding scrape-jobs" in out and "roles/cloudtasks.enqueuer" in out
    assert "run jobs add-iam-policy-binding mvp-scrape-worker" in out and "roles/run.developer" in out
    assert "roles/run.invoker" in out
    assert "secrets create scrape-worker-dispatch-token" in out
    assert "run jobs deploy mvp-scrape-worker" in out
    assert "--command python,-m,app.worker" in out
    assert "--set-cloudsql-instances proj:europe-west1:db" in out
    assert "--set-secrets API_KEY=api-key:latest" in out
    assert "--task-timeout 10800" in out and "--max-retries 0" in out


def test_infra_dry_run_never_prints_env_values_or_the_random_token(capsys):
    out = _dry_run(mod.stage_infra, capsys)
    assert DB_PASSWORD not in out
    assert "production" not in out.split("run jobs deploy")[1]  # env values travel in a temp file, not argv
    assert "random token via stdin" in out


def test_activate_points_the_api_at_the_job(capsys):
    out = _dry_run(mod.stage_activate, capsys)
    assert "JOB_DISPATCH_MODE=cloud_tasks" in out
    assert "CLOUD_RUN_SCRAPE_JOB_NAME=mvp-scrape-worker" in out
    assert "WORKER_DISPATCH_URL=https://mvp-backend-abc.europe-west1.run.app" in out
    assert "WORKER_DISPATCH_SERVICE_ACCOUNT=mvp-tasks-invoker@proj.iam.gserviceaccount.com" in out
    assert "WORKER_DISPATCH_TOKEN=scrape-worker-dispatch-token:latest" in out


def test_rollback_restores_local_dispatch_and_cpu(capsys):
    out = _dry_run(mod.stage_rollback, capsys)
    assert "JOB_DISPATCH_MODE=local" in out and "--no-cpu-throttling" in out


def test_dangerous_roles_are_never_granted_project_wide(capsys):
    out = _dry_run(mod.stage_infra, capsys)
    assert "projects add-iam-policy-binding" not in out
