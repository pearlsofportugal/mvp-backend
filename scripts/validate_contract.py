"""Walk GET /api/v2/listings by cursor and validate every listing against the published schema.

Read-only. Prints, per partner, how many listings violate the schema and how often each
`quality.issues` code shows up, plus how many are publishable. Exits 1 if any listing violates
the schema, so it can gate a release.

    set MVP_API_URL=https://<host>/api/v1        (the /api/v2 root is derived from it)
    set MVP_API_KEY=<key>
    python scripts/validate_contract.py
    python scripts/validate_contract.py --partner realkey --limit 200 --show 5
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

import jsonschema


def get(url: str, key: str | None) -> dict:
    headers = {"X-API-Key": key} if key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"GET {url} -> HTTP {exc.code}: {exc.read()[:300]!r}")


def v2_root(api_url: str) -> str:
    root = re.sub(r"/api/v\d+/?$", "", api_url.rstrip("/"))
    return f"{root}/api/v2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--partner", help="only this partner_key")
    parser.add_argument("--status", default="all", choices=["active", "removed", "all"])
    parser.add_argument("--limit", type=int, default=200, help="page size (1-200)")
    parser.add_argument("--show", type=int, default=3, help="violations to print per partner")
    args = parser.parse_args()

    api_url, key = os.environ.get("MVP_API_URL", ""), os.environ.get("MVP_API_KEY", "")
    if not api_url or not key:
        sys.exit("Set MVP_API_URL (.../api/v1) and MVP_API_KEY first.")
    root = v2_root(api_url)

    schema = get(f"{root}/schemas/listing/2.0.json", None)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    total: Counter = Counter()
    invalid: Counter = Counter()
    publishable: Counter = Counter()
    issues: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    duplicates = 0
    cursor = None
    while True:
        params = {"limit": args.limit, "status": args.status}
        if args.partner:
            params["partner"] = args.partner
        if cursor:
            params["cursor"] = cursor
        page = get(f"{root}/listings?{urllib.parse.urlencode(params)}", key)
        for item in page["items"]:
            partner = (item.get("source") or {}).get("partner_key", "?")
            total[partner] += 1
            if item["id"] in seen:
                duplicates += 1
            seen.add(item["id"])
            errors = sorted(validator.iter_errors(item), key=lambda e: list(e.path))
            if errors:
                invalid[partner] += 1
                if len(examples[partner]) < args.show:
                    first = errors[0]
                    examples[partner].append(f"{item['id']} {'/'.join(map(str, first.path)) or '<root>'}: {first.message[:160]}")
            quality = item.get("quality") or {}
            publishable[partner] += bool(quality.get("publishable"))
            issues[partner].update(quality.get("issues") or [])
        cursor = page.get("next_cursor")
        print(f"\r{len(seen)} / {page['total']} listings", end="", file=sys.stderr, flush=True)
        if not cursor:
            break
    print(file=sys.stderr)

    print(f"{'partner':<18}{'listings':>9}{'invalid':>9}{'publishable':>13}  top quality issues")
    for partner in sorted(total):
        top = ", ".join(f"{code} {n}" for code, n in issues[partner].most_common(4))
        print(f"{partner:<18}{total[partner]:>9}{invalid[partner]:>9}{publishable[partner]:>13}  {top}")
        for line in examples[partner]:
            print(f"    ! {line}")
    print(f"\n{sum(total.values())} listings, {sum(invalid.values())} violating the schema, {duplicates} repeated across pages")
    sys.exit(1 if invalid or duplicates else 0)


if __name__ == "__main__":
    main()
