# eGO Real Estate platform adapter

`app/services/ego_extract_service.py`

eGO Real Estate (`egorealestate.com`) is a white-label website platform used by
hundreds of Portuguese agencies. Every eGO-generated site renders its listing
detail page from the same client-side template, so **one adapter covers any eGO
domain** — no per-site `SiteConfig`, no selector onboarding.

This is why the backend now runs a headless browser on a user-facing request
path, which drives the Cloud Run deploy parameters in `cloudbuild.yaml`.

## Where it sits

The adapter is part of the generic `/api/v1/ingest` path, not the scheduled
scraper:

```
POST /api/v1/ingest
  └─ ingest_service.ingest_listing()
       ├─ host matches an active SiteConfig → partner pipeline (unchanged)
       └─ no match → generic_extract_service.extract_generic()
                       ├─ _fetch()  — static HTML (EthicalScraper)
                       ├─ looks_like_ego_platform(html)?  ── yes ─┐
                       │                                          │
                       │   _extract_via_ego():                    │
                       │     _fetch_rendered()  → Playwright       │
                       │     extract_ego_platform(html, url)       │
                       │     normalize_ego_platform_payload(...)   │
                       │                                          │
                       └─ no (or eGO yielded nothing) ────────────┘
                            layered generic pipeline (a–e)
```

`normalize_ego_platform_payload()` in `mapper_service.py` is the same normalizer
already used by the hand-configured eGO partners (`t2mais1`, `imobiliariaprp`,
`escolhacerta`, ...), so the adapter's output dict is deliberately shaped like
those partner parsers' output.

## Detection

`looks_like_ego_platform(html)` runs on the **static** HTML — eGO always ships
its asset/bundle references server-side even though the listing data is not
there. It requires `egorealestate` plus one of the markers in `_EGO_MARKERS`
(`egoforge/websiteeditor`, `ep.egorealestate.com`,
`websiteapi.egorealestate.com`, `dataapi.realestates`).

## Why it always renders

The listing payload is injected by JavaScript after the initial HTML. eGO
server-renders some blocks but lazy-loads others (gallery, agent sidebar, parts
of the specs), so `_extract_via_ego()` renders unconditionally rather than
trying to decide per page. If rendering fails or returns a challenge page, it
falls back to the static HTML it already has; if that yields neither a title nor
a price, it returns `None` and the generic pipeline takes over.

**Cost.** Each render constructs a fresh `PlaywrightScraper` and closes it
afterwards, so every eGO ingest pays a cold Chromium launch — nothing is pooled
or kept warm between requests, and `--min-instances=1` does not change this.

## What it extracts

| Source in the page | Fields |
|---|---|
| `.propertyDetails li.detailItem` label/value table | district, county, parish, condition, property_id, property_type, typology, useful_area, gross_area, land_area, construction_year |
| `Venda` / `Arrendamento` / `Trespasse` detail row | business_type, price |
| `i.energyClass` CSS class | energy_certificate |
| icon strip / `.wb-fld-*` | bedrooms, bathrooms, price and business_type fallbacks |
| `.specsText` | raw_description |
| `.agentName` / `a[href^="tel:"]` | advertiser, contacts |
| `img.Streamed` / `[data-imgthumb]` | images (+ empty `alt_texts`) |
| `<title>`, `meta[name=description]` | page_title, meta_description |

Two gallery details worth knowing, both in `_extract_gallery()`:

- Thumbnails are grouped by the `/P<number>/` segment and only the **dominant**
  property number is kept, which drops the related-listings carousel and the eGO
  CRM logo.
- The `/Z<w>x<h>/` size segment is normalised to `/Z1280x960/` so the same photo
  at two sizes dedupes to one entry.

`ego_source_partner(url)` derives `source_partner` from the hostname, e.g.
`https://www.exemplo.pt/...` → `ego_exemplo_pt`.

## Adding coverage

The adapter is selector-based against the shared eGO template, so a site that
extracts poorly is usually a template variant, not a new platform. Extend the
selector lists in `extract_ego_platform()` (they are already comma-separated
alternatives) or add the label to `_DETAIL_LABEL_MAP` — accented and
unaccented spellings are both listed because eGO sites differ. Follow the
existing partner practice and add a regression test with a realistic HTML
snippet.

## Note on robots.txt

The `/ingest` path constructs its scrapers with `respect_robots=False`
(`generic_extract_service._fetch_static` / `_fetch_rendered`), on the rationale
recorded there that the caller is a human pasting a single link they are already
viewing. This is a deliberate exception to the fail-closed rule described under
"Ethical Scraping Rules" in the README, which still governs the scheduled
scraping jobs. Note that robots.txt is still **fetched** on the Playwright path
(up to a 15 s timeout in `PlaywrightScraper._load_robots`) — only its verdict is
ignored, so the request pays the cost either way.
