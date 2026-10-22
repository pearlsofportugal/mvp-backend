"""Give every site a weekly, staggered Cloud Scheduler job through the HTTP API.

Dry-run by default: prints the schedule it would set (with the cron the app derives from
it). With --apply it PATCHes each site (schedule_enabled, interval, start time, start URL,
page limit), which makes the app create/update the Cloud Scheduler job `scrape-<site>`,
then reads the job back and warns when no next run exists.

    set MVP_API_URL=https://<host>/api/v1
    set MVP_API_KEY=<key>
    python scripts/schedule_sites.py                 # show the plan
    python scripts/schedule_sites.py --apply
    python scripts/schedule_sites.py --apply --only t2mais1

Needs GOOGLE_CLOUD_PROJECT and BACKEND_URL in the service environment and permission to
manage Cloud Scheduler for its service account; otherwise the PATCH succeeds but no job is
created (the check at the end reports it).
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import timezone
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.cloud_scheduler_service import _to_cron  # noqa: E402

TZ = "Europe/Lisbon"
WEEKLY_MINUTES = 7 * 24 * 60
MAX_PAGES = 60  # the crawl stops by itself after two pages without new listings

# Reference week used only to encode "weekday + wall-clock time" as schedule_start_at.
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


def start_at_utc(day: tuple[int, int, int], hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(*day, hour, minute, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)


def start_url_for(site: dict) -> str:
    """Where a full run starts: the sitemap for sitemap sites, else the first listing page."""
    start = site.get("schedule_start_url") or site["base_url"]
    if site["pagination_type"] == "sitemap":
        origin = urlparse(site["base_url"])
        return start if start.endswith(".xml") else f"{origin.scheme}://{origin.netloc}/sitemap.xml"
    return re.sub(r"([?&]pg=)\d+", r"\g<1>1", start)  # a stored start URL may point at page 2


def build_patch(site: dict) -> dict | None:
    slot = SLOTS.get(site["key"])
    if slot is None:
        return None
    start = start_at_utc(*slot)
    return {
        "schedule_enabled": True,
        "schedule_interval_minutes": WEEKLY_MINUTES,
        "schedule_timezone": TZ,
        "schedule_start_at": start.isoformat().replace("+00:00", "Z"),
        "schedule_start_url": start_url_for(site),
        "schedule_max_pages": MAX_PAGES,
    }


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
    parser.add_argument("--apply", action="store_true", help="PATCH the sites (default: only print the plan)")
    parser.add_argument("--only", help="comma-separated site keys")
    args = parser.parse_args()
    base, key = os.environ.get("MVP_API_URL", "").rstrip("/"), os.environ.get("MVP_API_KEY", "")
    if not base or not key:
        sys.exit("Set MVP_API_URL (.../api/v1) and MVP_API_KEY first.")

    _, resp = call("GET", f"{base}/sites?page_size=100", key)
    sites = resp["data"] if isinstance(resp["data"], list) else resp["data"]["items"]
    wanted = set(args.only.split(",")) if args.only else None
    known = {s["key"] for s in sites}
    for missing in sorted(set(SLOTS) - known):
        print(f"[warn]    {missing} has a slot but is not a configured site")

    problems = 0
    for site in sorted((s for s in sites if s["is_active"] and s["key"] in SLOTS), key=lambda s: start_at_utc(*SLOTS[s["key"]])):
        if wanted and site["key"] not in wanted:
            continue
        patch = build_patch(site)
        start = datetime.fromisoformat(patch["schedule_start_at"].replace("Z", "+00:00"))
        cron = _to_cron(WEEKLY_MINUTES, start, TZ)
        print(f"{site['key']:18} {cron:14} ({TZ})  {patch['schedule_start_url']}")
        if not args.apply:
            continue
        status, result = call("PATCH", f"{base}/sites/{site['key']}", key, patch)
        if status != 200:
            problems += 1
            print(f"    PATCH failed (HTTP {status}): {str(result)[:200]}")
            continue
        _, sched = call("GET", f"{base}/sites/{site['key']}/schedule", key)
        next_run = (sched.get("data") or {}).get("next_run_at")
        if next_run:
            print(f"    ok, next run {next_run}")
        else:
            problems += 1
            print("    saved, but Cloud Scheduler has no job for it (check GOOGLE_CLOUD_PROJECT / BACKEND_URL / permissions)")

    if not args.apply:
        print("\nDry run - nothing changed. Use --apply (optionally --only t2mais1).")
    elif problems:
        sys.exit(f"\n{problems} site(s) need attention.")


if __name__ == "__main__":
    main()
