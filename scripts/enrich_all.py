"""Run the AI enrichment (multi-locale SEO content) over every listing, in batches, through the HTTP API.

Dry-run by default (prints how many listings are enriched / pending per partner); pass --apply to run it.
The API enriches at most 200 listings per call and, without --force, only those that have no
translations yet; this script keeps calling it until nothing is pending. Each batch is awaited
before the next one starts.

    set MVP_API_URL=https://<host>/api/v1
    set MVP_API_KEY=<key>
    python scripts/enrich_all.py                              # show the plan
    python scripts/enrich_all.py --apply --only bassolioporto
    python scripts/enrich_all.py --apply                      # every listing that is not enriched yet
    python scripts/enrich_all.py --apply --force              # redo ALL listings, enriched ones included
"""
import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

POLL_SECONDS = 10
BATCH = 200
FINAL = {"completed", "failed"}


def call(method: str, url: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    """HTTP call returning (status, json). Connection failures are retried, then reported as status 0."""
    for attempt in range(1, 5):
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
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, OSError) as exc:
            print(f"    connection problem ({type(exc).__name__}), attempt {attempt}/4", flush=True)
            time.sleep(15 * attempt)
    return 0, {}


def run_batch(base: str, key: str, body: dict) -> tuple[int, int, int, list[str]] | None:
    """Start one bulk job and wait for it. Returns (total, done, failed, errors), or None if it could not start."""
    status, resp = call("POST", f"{base}/enrichment/ai/bulk", key, body)
    if status != 202:
        print(f"  could not start (HTTP {status}): {str(resp)[:200]}")
        return None
    accepted = resp["data"]
    total, job_id = accepted["total"], accepted["job_id"]
    if total == 0:
        return 0, 0, 0, []
    print(f"  job {job_id}: {total} listing(s)", flush=True)
    while True:
        time.sleep(POLL_SECONDS)
        code, job = call("GET", f"{base}/enrichment/ai/bulk/jobs/{job_id}", key)
        if code == 0:
            continue  # API unreachable for now; the job keeps running server-side
        data = job.get("data") or {}
        print(f"    {data.get('status')}  done={data.get('done')} failed={data.get('failed')} "
              f"skipped={data.get('skipped')} of {data.get('total')}", flush=True)
        if data.get("status") in FINAL:
            return total, data.get("done", 0), data.get("failed", 0), data.get("errors") or []


def all_listing_ids(base: str, key: str, partner: str | None) -> list[str]:
    ids: list[str] = []
    page = 1
    while True:
        query = {"page": page, "page_size": 100}
        if partner:
            query["source_partner"] = partner
        _, resp = call("GET", f"{base}/listings/selector?{urllib.parse.urlencode(query)}", key)
        items = (resp.get("data") or {}).get("items") or []
        ids += [item["id"] for item in items]
        if page >= ((resp.get("meta") or {}).get("pages") or 0):
            return ids
        page += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="run the enrichment (default: only print the plan)")
    parser.add_argument("--only", help="comma-separated source partners (site keys)")
    parser.add_argument("--force", action="store_true", help="regenerate listings that are already enriched too")
    args = parser.parse_args()
    base, key = os.environ.get("MVP_API_URL", "").rstrip("/"), os.environ.get("MVP_API_KEY", "")
    if not base or not key:
        sys.exit("Set MVP_API_URL (…/api/v1) and MVP_API_KEY first.")

    wanted = args.only.split(",") if args.only else None
    status, resp = call("GET", f"{base}/enrichment/ai/stats", key)
    if status != 200:
        sys.exit(f"could not read the enrichment stats (HTTP {status})")
    by_source = resp["data"]["by_source"]
    partners = [p for p in sorted(by_source) if not wanted or p in wanted]
    pending = 0
    for partner in partners:
        total, done = by_source[partner]["total"], by_source[partner]["enriched_count"]
        pending += total - done
        print(f"{partner:18} total={total:<5} enriched={done:<5} pending={total - done}")
    print(f"{'':18} to process: {sum(by_source[p]['total'] for p in partners) if args.force else pending}")
    if not args.apply:
        print("\nDry run - nothing started. Use --apply (optionally --only bassolioporto, --force).")
        return

    for partner in partners:
        print(f"\n{partner}", flush=True)
        if args.force:
            ids = all_listing_ids(base, key, partner)
            batches = [{"listing_ids": ids[i:i + BATCH], "force": True} for i in range(0, len(ids), BATCH)]
        else:
            batches = None
        n = 0
        while True:
            body = batches[n] if batches is not None else {"limit": BATCH, "source_partner": partner}
            if batches is not None and n >= len(batches):
                break
            result = run_batch(base, key, body)
            if result is None:
                break
            total, done, failed, errors = result
            n += 1
            if total == 0:
                break
            if batches is None and done == 0:
                # Failed listings stay "not enriched" and would be picked up again forever.
                print(f"  nothing was enriched in this batch ({failed} failed) - stopping {partner}")
                for err in errors[:3]:
                    print(f"    {err[:200]}")
                break


if __name__ == "__main__":
    main()
