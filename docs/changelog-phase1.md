# Phase 1 — lifecycle, reliable `updated_at`, coordinates, full gallery

Everything here is additive: no field or endpoint was removed or renamed, and
the defaults seen by an existing client are unchanged.

## What changed

| Area | Change |
|---|---|
| Lifecycle | `listings.status` (`active`/`removed`), `first_seen_at`, `last_seen_at`, `removed_at`. `status` is in the list and detail responses. |
| Filters | `GET /api/v1/listings?status=active\|removed\|all` (default `active`), `updated_after`, `updated_before`. Documented in OpenAPI. Stats, `/selector`, `/duplicates` and exports also default to `active`. Detail by id still serves removed listings. |
| Removal | Sold listings, listings absent from a crawl (same 10-URL / 40% safeguards) and URLs answering 404/410 are **soft-deleted** (`status=removed`) instead of hard-deleted. A removed listing that comes back is reactivated. `DELETE /listings/{id}` is still a hard delete. |
| Junk pages | Playwright no longer returns the rendered error page for 404/410. A page that parses to a numeric/empty title with no type and no price is never stored and never overwrites a good record (`is_junk_listing`). |
| `updated_at` | Only moves when the normalised content or the gallery URLs change, or a removed listing returns. A re-scrape of unchanged content only moves `last_seen_at`. `content_hash` stores the fingerprint. |
| Coordinates | `latitude`/`longitude` read from JSON-LD, meta tags, microdata, `data-*` attributes, map links/embeds and inline scripts; ambiguous or non-Portuguese points are dropped; the agency's own office is ignored. New `location_precision` (`exact` when the page declared it). List responses now include all three. |
| Gallery | Every photo is stored (junk filtered, cover first) with `position`; `width`/`height` when the source states them. The stored gallery is only rewritten when it differs. Media is returned ordered by `position`. |

### Deliberately not done
- **No geocoding.** Listings whose page carries no coordinates keep `latitude`/`longitude`/`location_precision` = `null`. The acceptance criterion "Porto listings with lat/lng by parish" is therefore only met for sources that publish coordinates; `parish`/`county` precision would need a geocoder or the official parish centroids (Phase 2, geo normalisation).
- No `original_url` column: `media_assets.url` already is the source URL (a CDN URL will be a separate `url_cdn`, Phase 3 #13).

## Deploying

Migrations run on container start (`alembic upgrade head` in the Dockerfile). All four are DDL-only plus two single-statement seeds, so they are quick and compatible with the previous revision still serving:

```bash
python -m alembic upgrade head      # f6a7b8c9d0e1 → a7b8c9d0e1f2 → b8c9d0e1f2a3 → c9d0e1f2a3b4
```

Then, once, archive the existing "410" records (nothing is deleted; dry-run by default):

```bash
python -m scripts.mark_junk_listings_removed            # report
python -m scripts.mark_junk_listings_removed --apply    # CSV backup in ./backups, then status='removed'
python -m scripts.backfill_content_hash                 # optional; idempotent, never touches updated_at
```

### Expect one wave of `updated_at` changes
Listings whose stored gallery was the single cover photo (the previous behaviour) will gain their full gallery on the next scrape, and that **is** a content change, so the WordPress sync will re-fetch them once. After that, consecutive scrapes of unchanged listings leave `updated_at` alone. Rows without a hash are compared against a hash recomputed from the stored row, so they do not cause an extra wave.

## Rolling back

```bash
python -m alembic downgrade e5f6a7b8c9d0     # drops the new columns/index only
```

The migrations are additive, so reverting the code alone is also safe (old code ignores the new columns). To un-archive the "410" rows, set `status='active'` for the ids in the CSV backup. Rows soft-deleted by the scraper can be listed with `?status=removed`.

## Tests

`python -m pytest tests -q`. New: `test_lifecycle.py`, `test_listing_lifecycle.py`, `test_upsert_updated_at.py` (two upserts leave `updated_at` untouched), `test_coordinates.py`, `test_gallery.py`.

Not covered by the suite: the PostgreSQL `INSERT … ON CONFLICT` branch (tests run on SQLite and exercise the shared update path) and the migrations themselves (tests use `create_all`). The migration SQL was reviewed offline with `alembic upgrade e5f6a7b8c9d0:head --sql`; run the upgrade/downgrade once against a Postgres before deploying.
