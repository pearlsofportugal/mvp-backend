"""Set up (and run) the Cloud Run Job that does the AI SEO enrichment outside the API service.

The API's /enrichment/ai/bulk runs inside the service: one listing at a time, state in memory, a
45 s Gemini timeout. The Job has its own container, a long timeout and resumes from the database
(it only selects listings that are still not enriched), so it is the way to do thousands of listings.

Dry-run by default: prints every command (values masked); add --apply to run it.

  python scripts/setup_enrich_worker.py --apply                       # create / update the Job
  python scripts/setup_enrich_worker.py --apply --execute --partner realkey      # run it on one site
  python scripts/setup_enrich_worker.py --apply --execute                         # every pending listing
  python scripts/setup_enrich_worker.py --apply --execute --force --partner habita  # regenerate

The Job copies image, service account, Cloud SQL instance, env vars and secrets from the running
service (like scripts/setup_scrape_worker.py), and gets a longer Gemini timeout. Needs an authenticated gcloud.
Follow an execution with:  gcloud run jobs executions list --job mvp-enrich-worker --region europe-west1
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_scrape_worker import Gcloud, service_info  # noqa: E402  (same gcloud helpers, same dry-run rules)

GEMINI_TIMEOUT_MS = "120000"  # the service default (45 s) is too short for 5 locales of 250-400 words


def deploy_job(g: Gcloud, a) -> None:
    info = service_info(g, a)
    print(f"\nCloud Run Job {a.job} (image of {a.service}: {info['image'].split('@')[0].rsplit('/', 1)[-1]})")
    args = ["run", "jobs", "deploy", a.job, "--region", a.region, "--project", a.project, "--image", info["image"],
            "--command", "python,-m,app.enrich_worker", "--cpu", "1", "--memory", "1Gi",
            "--task-timeout", "86400", "--max-retries", "0", "--tasks", "1", "--parallelism", "1",
            "--service-account", info["sa"]]
    if info["sql"]:
        args += ["--set-cloudsql-instances", info["sql"]]
    if info["secrets"]:
        args += ["--set-secrets", ",".join(f"{k}={v}" for k, v in info["secrets"].items())]
    env = {**info["env"], "GOOGLE_GENAI_TIMEOUT_MS": GEMINI_TIMEOUT_MS}
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:  # 0600, values never reach the command line
        json.dump(env, fh)
    args += ["--env-vars-file", tmp]
    try:
        g.run(*args, note=f"[env from {len(env)} vars, values hidden]")
    finally:
        os.unlink(tmp)
    # Only newer versions of the shared helper collect refused IAM bindings.
    getattr(g, "report_denied", lambda: None)()


def execute_job(g: Gcloud, a) -> None:
    job_args = []
    if a.partner:
        job_args += ["--partner", a.partner]
    if a.force:
        job_args.append("--force")
    if a.limit:
        job_args += ["--limit", str(a.limit)]
    print(f"\nExecute {a.job}" + (f" with {' '.join(job_args)}" if job_args else " (every pending listing)"))
    cmd = ["run", "jobs", "execute", a.job, "--region", a.region, "--project", a.project]
    if job_args:
        cmd += ["--args", ",".join(job_args)]
    g.run(*cmd)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="run the commands (default: only print them)")
    p.add_argument("--execute", action="store_true", help="after deploying, start one execution of the Job")
    p.add_argument("--partner", default="", help="with --execute: comma-separated source partners")
    p.add_argument("--force", action="store_true", help="with --execute: regenerate already enriched listings too")
    p.add_argument("--limit", type=int, default=0, help="with --execute: stop after this many listings")
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "rock-voice-352108"))
    p.add_argument("--region", default="europe-west1")
    p.add_argument("--service", default="mvp-backend")
    p.add_argument("--job", default="mvp-enrich-worker")
    a = p.parse_args()

    g = Gcloud(a.apply)
    print(f"{'APPLY' if a.apply else 'DRY-RUN'}  project={a.project} region={a.region} service={a.service} job={a.job}")
    deploy_job(g, a)
    if a.execute:
        execute_job(g, a)
    if not a.apply:
        print("\nDry run - nothing changed. Add --apply to execute.")


if __name__ == "__main__":
    main()
