"""Weekly, staggered Cloud Scheduler job per site (`weekly-<site>`), calling the trigger endpoint.

Each job does `POST <backend>/api/v1/jobs/trigger/<site>` (header X-API-Key); the API creates the
scrape job and dispatches it (to the Cloud Run Job once JOB_DISPATCH_MODE=cloud_tasks).

Dry-run by default: prints, per site, the cron and the gcloud command. With --apply it
  1. PATCHes the site's `schedule_start_url` and `schedule_max_pages` (the trigger reads them), and
  2. creates the Scheduler job with gcloud (or updates it when it already exists).
`schedule_enabled` is deliberately NOT set: with it the app would manage jobs named `scrape-<site>`
itself (and delete them on any later PATCH); these `weekly-` jobs are managed here.

    set MVP_API_URL=https://<host>/api/v1
    set MVP_API_KEY=<key>
    python scripts/schedule_sites.py                      # show the plan
    python scripts/schedule_sites.py --apply --only t2mais1
    python scripts/schedule_sites.py --apply

Needs an authenticated gcloud with Cloud Scheduler admin rights for --apply.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.cloud_scheduler_service import _to_cron  # noqa: E402

TZ = "Europe/Lisbon"
WEEKLY_MINUTES = 7 * 24 * 60
MAX_PAGES = 60  # the crawl stops by itself after two pages without new listings
JOB_PREFIX = "weekly-"
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"

# Reference week used only to encode "weekday + wall-clock time" as a datetime for _to_cron.
SUNDAY = (2026, 11, 1)
MONDAY = (2026, 11, 2)

# site key -> (reference day, "HH:MM" Europe/Lisbon). Big JS sites get >= 1 h to themselves
# (measured: entreparedes ~50 min, habita ~57 min); the small ones share the early morning.
SLOTS: dict[str, tuple[tuple[int, int, int], str]] = {
    "entreparedes": (SUNDAY, "01:00"),
    "habita": (SUNDAY, "02:30"),
    "barcelcasa": (SUNDAY, "04:00"),
    "brightmangroup_pt": (SUNDAY, "05:00"),
    "casa10_pt": (SUNDAY, "06:00"),
    "imobiliariaprp": (MONDAY, "01:00"),
    "mysquare": (MONDAY, "02:00"),
    "habinedita": (MONDAY, "03:30"),
    "sunpoint": (MONDAY, "05:00"),
    "sottomayor": (MONDAY, "05:30"),
    "escolhacerta": (MONDAY, "06:00"),
    "t2mais1": (MONDAY, "06:15"),
    "imoantlantico": (MONDAY, "06:30"),
    "realkey": (MONDAY, "07:00"),
}


def slot_datetime(day: tuple[int, int, int], hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(*day, hour, minute, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)


def cron_for(site_key: str) -> str:
    return _to_cron(WEEKLY_MINUTES, slot_datetime(*SLOTS[site_key]), TZ)


def start_url_for(site: dict) -> str:
    """Where a full run starts: the sitemap for sitemap sites, else the first listing page."""
    start = site.get("schedule_start_url") or site["base_url"]
    if site["pagination_type"] == "sitemap":
        origin = urlparse(site["base_url"])
        return start if start.endswith(".xml") else f"{origin.scheme}://{origin.netloc}/sitemap.xml"
    return re.sub(r"([?&]pg=)\d+", r"\g<1>1", start)  # a stored start URL may point at page 2


def scheduler_args(verb: str, site_key: str, backend_url: str, api_key: str, project: str, location: str) -> list[str]:
    """gcloud arguments to create/update the weekly job of a site (verb: 'create' or 'update')."""
    return [
        "scheduler", "jobs", verb, "http", f"{JOB_PREFIX}{site_key}",
        "--project", project, "--location", location,
        "--schedule", cron_for(site_key), "--time-zone", TZ,
        "--uri", f"{backend_url}/api/v1/jobs/trigger/{site_key}", "--http-method", "POST",
        "--update-headers" if verb == "update" else "--headers", f"X-API-Key={api_key}",
        "--attempt-deadline", "180s", "--max-retry-attempts", "2", "--min-backoff", "30s",
    ]


def printable(args: list[str]) -> str:
    """The command with the API key hidden."""
    return "gcloud " + " ".join(re.sub(r"(X-API-Key=).*", r"\1***", a) for a in args)


def call(method: str, url: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"{method} {url} -> {type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="PATCH the sites and create/update the jobs (default: print)")
    parser.add_argument("--only", help="comma-separated site keys")
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "rock-voice-352108"))
    parser.add_argument("--location", default="europe-west1")
    parser.add_argument("--backend-url", help="public URL of the API (default: MVP_API_URL without /api/v1)")
    args = parser.parse_args()
    base, key = os.environ.get("MVP_API_URL", "").rstrip("/"), os.environ.get("MVP_API_KEY", "")
    if not base or not key:
        sys.exit("Set MVP_API_URL (.../api/v1) and MVP_API_KEY first.")
    backend_url = (args.backend_url or re.sub(r"/api/v1$", "", base)).rstrip("/")

    _, resp = call("GET", f"{base}/sites?page_size=100", key)
    sites = resp["data"] if isinstance(resp["data"], list) else resp["data"]["items"]
    wanted = set(args.only.split(",")) if args.only else None
    for missing in sorted(set(SLOTS) - {s["key"] for s in sites}):
        print(f"[warn]    {missing} has a slot but is not a configured site")

    problems = 0
    todo = sorted((s for s in sites if s["is_active"] and s["key"] in SLOTS), key=lambda s: slot_datetime(*SLOTS[s["key"]]))
    for site in todo:
        k = site["key"]
        if wanted and k not in wanted:
            continue
        start_url = start_url_for(site)
        print(f"{k:18} {cron_for(k):13} ({TZ})  {start_url}")
        if not args.apply:
            print("    " + printable(scheduler_args("create", k, backend_url, key, args.project, args.location)))
            continue

        status, result = call("PATCH", f"{base}/sites/{k}", key,
                              {"schedule_start_url": start_url, "schedule_max_pages": MAX_PAGES})
        if status != 200:
            problems += 1
            print(f"    PATCH failed (HTTP {status}): {str(result)[:200]}")
            continue
        job = f"{JOB_PREFIX}{k}"
        exists = subprocess.run(
            [GCLOUD, "scheduler", "jobs", "describe", job, "--project", args.project, "--location", args.location],
            capture_output=True, text=True,
        ).returncode == 0
        cmd = scheduler_args("update" if exists else "create", k, backend_url, key, args.project, args.location)
        out = subprocess.run([GCLOUD, *cmd], capture_output=True, text=True)
        if out.returncode != 0:
            problems += 1
            print(f"    gcloud failed: {out.stderr.strip()[:300]}")
        else:
            print(f"    {'updated' if exists else 'created'} {job}")

    if not args.apply:
        print("\nDry run - nothing changed. Use --apply (optionally --only t2mais1).")
    elif problems:
        sys.exit(f"\n{problems} site(s) need attention.")


if __name__ == "__main__":
    main()
