"""Re-scrape every active site through the HTTP API, one site at a time.

Dry-run by default (prints the jobs it would start); pass --apply to start them.
Each job is awaited before the next one starts, so the service never runs more than
one scrape at once. Sites that already have a running job (HTTP 409) are skipped.

    set MVP_API_URL=https://<host>/api/v1
    set MVP_API_KEY=<key>
    python scripts/rescrape_sites.py                       # show the plan
    python scripts/rescrape_sites.py --apply --only realkey
    python scripts/rescrape_sites.py --apply               # all sites
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

POLL_SECONDS = 30
FINAL = {"completed", "failed", "cancelled"}


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


def start_url_for(site: dict) -> str:
    """Where a full run starts: the sitemap for sitemap sites, else the listing page."""
    start = site.get("schedule_start_url") or site["base_url"]
    if site["pagination_type"] == "sitemap":
        origin = urlparse(site["base_url"])
        return start if start.endswith(".xml") else f"{origin.scheme}://{origin.netloc}/sitemap.xml"
    # a scheduled start URL may point at a later page (habinedita uses pg=2): begin at page 1
    return re.sub(r"([?&]pg=)\d+", r"\g<1>1", start)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="start the jobs (default: only print the plan)")
    parser.add_argument("--only", help="comma-separated site keys")
    parser.add_argument("--max-pages", type=int, default=60, help="listing pages per site (ignored by sitemap sites)")
    args = parser.parse_args()
    base, key = os.environ.get("MVP_API_URL", "").rstrip("/"), os.environ.get("MVP_API_KEY", "")
    if not base or not key:
        sys.exit("Set MVP_API_URL (…/api/v1) and MVP_API_KEY first.")

    _, resp = call("GET", f"{base}/sites?page_size=100", key)
    sites = resp["data"] if isinstance(resp["data"], list) else resp["data"]["items"]
    wanted = set(args.only.split(",")) if args.only else None
    for site in sites:
        if not site["is_active"] or (wanted and site["key"] not in wanted):
            continue
        url = start_url_for(site)
        print(f"{site['key']:18} {site['pagination_type']:11} max_pages={args.max_pages:<3} {url}")
        if not args.apply:
            continue
        status, created = call("POST", f"{base}/jobs", key, {"site_key": site["key"], "start_url": url, "max_pages": args.max_pages})
        if status == 409:
            print("    already running - skipped"); continue
        if status != 201:
            print(f"    could not start (HTTP {status}): {str(created)[:200]}"); continue
        job_id = created["data"]["id"]
        print(f"    job {job_id} started", flush=True)
        while True:
            time.sleep(POLL_SECONDS)
            _, job = call("GET", f"{base}/jobs/{job_id}", key)
            data = job.get("data") or {}
            print(f"    {data.get('status')}  {json.dumps(data.get('progress'))}", flush=True)
            if data.get("status") in FINAL:
                break
    if not args.apply:
        print("\nDry run - nothing started. Use --apply (optionally --only realkey).")


if __name__ == "__main__":
    main()
