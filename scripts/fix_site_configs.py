"""Repair site configurations through the HTTP API (no database access needed).

What it does (dry-run by default; pass --apply to PATCH):
  1. Every site: collapse double-escaped backslashes in link_pattern / image_filter /
     image_exclude_filter (``imgs\\\\.x`` -> ``imgs\\.x``), the corruption that made the image
     filter reject every URL. The scraper also repairs these at run time, this cleans the data.
  2. mysquare: selectors for the current site markup (values live in ``.val``, gallery in
     ``.imagem-holder``), a link_pattern that matches the sitemap's ``/pt/imoveis/..._<id>/``
     URLs, and an image filter for the ximocrm image proxy.
  3. imoantlantico: first real configuration (HCPro CRM site, sitemap pagination).

Usage:
    set MVP_API_URL=https://<host>/api/v1
    set MVP_API_KEY=<key>
    python scripts/fix_site_configs.py            # show what would change
    python scripts/fix_site_configs.py --apply    # write it
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.utils.site_selectors import repair_regex  # noqa: E402

PATTERN_FIELDS = ("link_pattern", "image_filter", "image_exclude_filter")


def _has(label: str) -> str:
    """Value cell of the Imo-Atlântico info row whose label contains *label*."""
    return f".info-items-container .col-12:has(> .info-item-label:-soup-contains('{label}')) .info-item"


IMOATLANTICO = {
    "selectors": {
        "title_selector": "h1.left-section-heading",
        "price_selector": "p.preco",
        "property_id_selector": _has("Referência"),
        "business_type_selector": ".info-item.natureza",
        "property_type_selector": ".info-item.tipo-imovel",
        "typology_selector": ".info-item.tipologia",
        "condition_selector": _has("Estado"),
        "energy_certificate_selector": _has("Certificado energético"),
        "construction_year_selector": _has("Ano"),
        "district_selector": ".info-item.distrito",
        "county_selector": ".info-item.concelho",
        "parish_selector": ".info-item.freguesia",
        "area_selector": _has("Útil"),
        "gross_area_selector": _has("Área Bruta"),
        "land_area_selector": _has("Área de Terreno"),
        "description_selector": "#descricao pre",
        "images_selector": "#photo-slider img",
        "listing_link_selector": "a[href^='imovel-']",
    },
    "extraction_mode": "direct",
    "pagination_type": "sitemap",
    "link_pattern": r"^https://www\.imo-atlantico\.pt/imovel-[a-z0-9-]+-\d+$",
    "image_filter": r"multimedia\.hcpro\.pt/hcpro/\d+/properties/",
    "use_js_render": False,
    "schedule_start_url": "https://www.imo-atlantico.pt/sitemap.xml",
}

MYSQUARE_VAL_SELECTORS = (
    "price_selector", "district_selector", "county_selector", "parish_selector",
    "typology_selector", "condition_selector", "property_type_selector",
)


def mysquare_patch(site: dict) -> dict:
    selectors = dict(site["selectors"])
    for key in MYSQUARE_VAL_SELECTORS:
        if selectors.get(key) and not selectors[key].endswith(" .val"):
            selectors[key] += " .val"
    selectors["images_selector"] = ".imagem-holder .swiper-slide img"
    selectors["description_selector"] = ".bloco-descricao .descricao"
    return {
        "selectors": selectors,
        "link_pattern": r"^https://www\.mysquare\.pt/pt/imoveis/[^/?#]+_\d+/?$",
        "image_filter": r"ximocrm\.com/image-optimizer/resize:fit:1920",
    }


def build_patch(site: dict) -> dict:
    patch: dict = {}
    if site["key"] == "mysquare":
        patch.update(mysquare_patch(site))
    elif site["key"] == "imoantlantico":
        patch.update(IMOATLANTICO)
    for field in PATTERN_FIELDS:
        value = patch.get(field, site.get(field))
        fixed = repair_regex(value)
        if fixed != site.get(field):
            patch[field] = fixed
    # keep only real changes
    return {k: v for k, v in patch.items() if v != site.get(k)}


def call(method: str, url: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {url} -> HTTP {exc.code}: {exc.read().decode()[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="PATCH the changes (default: only print them)")
    args = parser.parse_args()
    base, key = os.environ.get("MVP_API_URL", "").rstrip("/"), os.environ.get("MVP_API_KEY", "")
    if not base or not key:
        sys.exit("Set MVP_API_URL (…/api/v1) and MVP_API_KEY first.")

    sites = call("GET", f"{base}/sites?page_size=100", key)["data"]
    sites = sites if isinstance(sites, list) else sites["items"]
    changed = 0
    for site in sites:
        patch = build_patch(site)
        if not patch:
            print(f"[ok]      {site['key']}")
            continue
        changed += 1
        print(f"[change]  {site['key']}: {', '.join(patch)}")
        for field, value in patch.items():
            if field == "selectors":
                old = site["selectors"]
                for k in sorted(set(value) | set(old)):
                    if value.get(k) != old.get(k):
                        print(f"            selectors.{k}: {old.get(k)!r} -> {value.get(k)!r}")
            else:
                print(f"            {field}: {site.get(field)!r} -> {value!r}")
        if args.apply:
            result = call("PATCH", f"{base}/sites/{site['key']}", key, patch)
            after = result["data"]
            bad = [f for f in PATTERN_FIELDS if after.get(f) != patch.get(f, after.get(f))]
            print("            applied" + (f" (but {bad} differ from what was sent)" if bad else ""))
    print(f"\n{changed} site(s) {'updated' if args.apply else 'would change (dry-run, use --apply)'}.")


if __name__ == "__main__":
    main()
