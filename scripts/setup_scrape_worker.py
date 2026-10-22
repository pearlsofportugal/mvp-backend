"""Set up the Cloud Run Job that runs scrapes outside the API service.

Flow once active:  Cloud Scheduler -> API (POST /api/v1/jobs/trigger/<site>) -> Cloud Task
-> API (/internal/workers/scrape) -> Cloud Run Job `mvp-scrape-worker` (python -m app.worker).
The API only creates and dispatches the job; the Job is billed only while it scrapes.

Stages (dry-run by default: prints every command, secrets/values masked; add --apply to run):

  python scripts/setup_scrape_worker.py              # stage 1: infrastructure, traffic untouched
  python scripts/setup_scrape_worker.py --activate   # stage 2: point the API at the Job (after testing)
  python scripts/setup_scrape_worker.py --rollback   # back to scrapes inside the API service

Stage 1 (idempotent): enables the APIs, creates the Cloud Tasks queue, the `mvp-tasks-invoker`
service account, the IAM bindings, the dispatch-token secret (random, never printed) and the Job,
copying image, service account, Cloud SQL instance, env vars and secret references from the
running service (values go through a 0600 temp file, never through the command line).

Requires an authenticated `gcloud` with Owner/Editor-level rights on the project.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

SECRET_NAME = "scrape-worker-dispatch-token"
INVOKER = "mvp-tasks-invoker"
APIS = ["run.googleapis.com", "cloudtasks.googleapis.com", "cloudscheduler.googleapis.com", "secretmanager.googleapis.com"]
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"


class Gcloud:
    def __init__(self, apply: bool):
        self.apply = apply

    def _show(self, args: list[str], note: str = "") -> None:
        print("  + gcloud " + " ".join(args) + (f"   {note}" if note else ""), flush=True)

    def read(self, *args: str) -> str:
        """Read-only call: always executed, even in dry-run."""
        out = subprocess.run([GCLOUD, *args], capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"gcloud {' '.join(args[:4])} failed:\n{out.stderr.strip()[:600]}")
        return out.stdout.strip()

    def exists(self, *args: str) -> bool:
        return subprocess.run([GCLOUD, *args], capture_output=True, text=True).returncode == 0

    def run(self, *args: str, stdin: str | None = None, note: str = "") -> None:
        """State-changing call: printed in dry-run, executed with --apply."""
        self._show(list(args), note)
        if not self.apply:
            return
        out = subprocess.run([GCLOUD, *args], input=stdin, capture_output=True, text=True)
        if out.returncode != 0:
            err = out.stderr.strip()
            if "ALREADY_EXISTS" in err or "already exists" in err:
                print("    (already exists, kept)")
                return
            sys.exit(f"    FAILED: {err[:600]}")


def service_info(g: Gcloud, a) -> dict:
    d = json.loads(g.read("run", "services", "describe", a.service, "--region", a.region, "--project", a.project, "--format=json"))
    tpl = d["spec"]["template"]
    c = tpl["spec"]["containers"][0]
    sa = tpl["spec"].get("serviceAccountName")
    if not sa:
        number = g.read("projects", "describe", a.project, "--format=value(projectNumber)")
        sa = f"{number}-compute@developer.gserviceaccount.com"
    plain, refs = {}, {}
    for item in c.get("env", []):
        ref = (item.get("valueFrom") or {}).get("secretKeyRef")
        if ref:
            refs[item["name"]] = f"{ref['name']}:{ref.get('key', 'latest')}"
        elif "value" in item:
            plain[item["name"]] = item["value"]
    return {
        "url": d["status"]["url"],
        "image": c["image"],
        "sa": sa,
        "sql": tpl["metadata"].get("annotations", {}).get("run.googleapis.com/cloudsql-instances"),
        "env": plain,
        "secrets": refs,
    }


def stage_infra(g: Gcloud, a) -> None:
    info = service_info(g, a)
    sa, project, region = info["sa"], a.project, a.region
    print(f"\nService {a.service}: image {info['image'].split('@')[0].rsplit('/', 1)[-1]}, runs as {sa}")
    print(f"  {len(info['env'])} plain env vars and {len(info['secrets'])} secret references will be copied to the Job\n")

    print("1. APIs")
    g.run("services", "enable", *APIS, "--project", project)

    print("2. Cloud Tasks queue")
    g.run("tasks", "queues", "create", a.queue, "--location", region, "--project", project,
          "--max-attempts", "5", "--min-backoff", "30s")
    g.run("tasks", "queues", "add-iam-policy-binding", a.queue, "--location", region, "--project", project,
          "--member", f"serviceAccount:{sa}", "--role", "roles/cloudtasks.enqueuer", note="[only this queue]")

    print("3. Service account that Cloud Tasks uses to call the API")
    invoker = f"{INVOKER}@{project}.iam.gserviceaccount.com"
    g.run("iam", "service-accounts", "create", INVOKER, "--project", project, "--display-name", "Cloud Tasks -> mvp-backend")
    g.run("run", "services", "add-iam-policy-binding", a.service, "--region", region, "--project", project,
          "--member", f"serviceAccount:{invoker}", "--role", "roles/run.invoker")

    print("4. Permissions of the API service account (enqueue: see 2; run the Job: see 6)")
    for target in (sa, invoker):  # act as itself (run the Job) and as the OIDC identity of the tasks
        g.run("iam", "service-accounts", "add-iam-policy-binding", target, "--project", project,
              "--member", f"serviceAccount:{sa}", "--role", "roles/iam.serviceAccountUser")

    print("5. Dispatch token secret")
    if g.exists("secrets", "describe", SECRET_NAME, "--project", project):
        print("  (secret exists, token kept)")
    else:
        g.run("secrets", "create", SECRET_NAME, "--project", project, "--replication-policy", "automatic",
              "--data-file=-", stdin=secrets.token_urlsafe(48), note="[random token via stdin]")
    g.run("secrets", "add-iam-policy-binding", SECRET_NAME, "--project", project,
          "--member", f"serviceAccount:{sa}", "--role", "roles/secretmanager.secretAccessor")

    print(f"6. Cloud Run Job {a.job}")
    args = ["run", "jobs", "deploy", a.job, "--region", region, "--project", project, "--image", info["image"],
            "--command", "python,-m,app.worker", "--cpu", "2", "--memory", "4Gi", "--task-timeout", "10800",
            "--max-retries", "0", "--tasks", "1", "--parallelism", "1", "--service-account", sa]
    if info["sql"]:
        args += ["--set-cloudsql-instances", info["sql"]]
    if info["secrets"]:
        args += ["--set-secrets", ",".join(f"{k}={v}" for k, v in info["secrets"].items())]
    tmp = None
    if info["env"]:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # mkstemp creates the file 0600
            json.dump(info["env"], fh)
        args += ["--env-vars-file", tmp]
    try:
        g.run(*args, note=f"[env from {len(info['env'])} vars, values hidden]" if info["env"] else "")
    finally:
        if tmp:
            os.unlink(tmp)
    # run.developer includes run.jobs.runWithOverrides, needed to pass --scrape-job-id; scoped to this Job only.
    g.run("run", "jobs", "add-iam-policy-binding", a.job, "--region", region, "--project", project,
          "--member", f"serviceAccount:{sa}", "--role", "roles/run.developer", note="[only this Job]")

    print("\nStage 1 done: nothing changed for traffic. Test, then run with --activate.")


def stage_activate(g: Gcloud, a) -> None:
    info = service_info(g, a)
    invoker = f"{INVOKER}@{a.project}.iam.gserviceaccount.com"
    env = {
        "JOB_DISPATCH_MODE": "cloud_tasks",
        "CLOUD_TASKS_PROJECT": a.project,
        "CLOUD_TASKS_LOCATION": a.region,
        "CLOUD_TASKS_QUEUE": a.queue,
        "WORKER_DISPATCH_URL": info["url"],
        "WORKER_DISPATCH_SERVICE_ACCOUNT": invoker,
        "CLOUD_RUN_SCRAPE_JOB_NAME": a.job,
        "GOOGLE_CLOUD_PROJECT": a.project,
        "BACKEND_URL": info["url"],
    }
    print("\nPoint the API at the Job (creates a new revision of the service)")
    g.run("run", "services", "update", a.service, "--region", a.region, "--project", a.project,
          "--update-env-vars", ",".join(f"{k}={v}" for k, v in env.items()),
          "--update-secrets", f"WORKER_DISPATCH_TOKEN={SECRET_NAME}:latest")
    print("\nNext: python scripts/rescrape_sites.py --apply --only t2mais1, then check the events of the job and\n"
          f"  gcloud run jobs executions list --job {a.job} --region {a.region} --project {a.project}")


def stage_rollback(g: Gcloud, a) -> None:
    print("\nBack to scrapes inside the API service (needs CPU allocated outside requests: --no-cpu-throttling)")
    g.run("run", "services", "update", a.service, "--region", a.region, "--project", a.project,
          "--update-env-vars", "JOB_DISPATCH_MODE=local", "--no-cpu-throttling")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="run the commands (default: only print them)")
    stage = p.add_mutually_exclusive_group()
    stage.add_argument("--activate", action="store_true", help="stage 2: switch the API to JOB_DISPATCH_MODE=cloud_tasks")
    stage.add_argument("--rollback", action="store_true", help="switch back to JOB_DISPATCH_MODE=local")
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "rock-voice-352108"))
    p.add_argument("--region", default="europe-west1")
    p.add_argument("--service", default="mvp-backend")
    p.add_argument("--job", default="mvp-scrape-worker")
    p.add_argument("--queue", default="scrape-jobs")
    a = p.parse_args()

    g = Gcloud(a.apply)
    print(f"{'APPLY' if a.apply else 'DRY-RUN'}  project={a.project} region={a.region} service={a.service} job={a.job}")
    (stage_rollback if a.rollback else stage_activate if a.activate else stage_infra)(g, a)
    if not a.apply:
        print("\nDry run - nothing changed. Add --apply to execute.")


if __name__ == "__main__":
    main()
