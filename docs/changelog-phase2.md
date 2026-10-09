# Phase 2 — vocabularies, clean descriptions, enrichment, geo codes, batch detail

All additive for the API *shape*: no field or endpoint was removed or renamed. Two things change **values**
that existing consumers read, by decision: `property_type` / `typology` are now closed vocabularies (#6).

## What changed

| # | Change |
|---|---|
| 5 Geo codes | `district_code` (2), `county_code` / DICO (4), `parish_code` / DICOFRE (6) on every listing, plus list filters `district_code`, `county_code`, `parish_code`. Matching tolerates case, accents, "União das freguesias de …", components of a union ("Massarelos"), small typos, and is scoped top-down so equal parish names in different municipalities never collide. The text columns are **not** rewritten. **Needs the dataset file** — see below; without it nothing is resolved and scrapes carry on. |
| 6 Vocabularies | `property_type` ∈ Apartamento, Moradia, Quinta, Terreno, Loja, Escritório, Armazém, Garagem, Prédio, Comercial, Outro (Penthouse/Duplex/Loft/Estúdio → Apartamento; Vivenda/Villa → Moradia; Lote → Terreno…). `typology` ∈ T0…T20, V3 → T3, Estúdio → T0; the `+1` of `T1+1` goes to the new `typology_extra`. The original wording stays in `raw_payload`. `GET /listings/vocabularies` lists both sets. Unrecognised types become `Outro` and are logged. |
| 7 `description_clean` | Drops the "Nota para agentes imobiliários…" trailer, AMI licence sentences and agency self-promotion; repairs `327. 000€` and ` . Fachada:` bullets. `description` is untouched. Per-partner rules go in `PARTNER_BOILERPLATE` (`services/description_cleaner.py`). |
| 8 Enrichment | `has_*`, `construction_year`, `condition`, `floor` are filled from explicit phrases when the source left them empty (never overriding the source; hedged mentions like "perto de piscina municipal" are ignored). `description_quality_score` is a free 0–100 heuristic over the clean text. When AI translations are applied, an empty `meta_description` is filled (pt, else en); the prompt now uses `description_clean`. EN and DE are already in the default locales. |
| 9 Batch detail | `GET /listings?include=detail` returns full detail records per page (media, price history, `description_clean`, status, codes…). Default format unchanged; `page_size` ≤ 50 with `include=detail`. |

## Deploying

```bash
python -m alembic upgrade head     # d0e1f2a3b4c5 → e1f2a3b4c5d6 → f2a3b4c5d6e7 (all additive columns)
```

Backfills (all dry-run by default, `--apply` to write). **Applying them changes what the API returns, so the
WordPress sync re-imports the affected listings once** (`updated_at` moves).

```bash
python -m scripts.backfill_vocabularies        # prints values that would become "Outro" — review before --apply
python -m scripts.backfill_description_clean
python -m scripts.backfill_attributes
python -m scripts.backfill_geo_codes           # needs app/data/freguesias.csv
```

Run the vocabulary report first: if a real partner value lands in `Outro`, add its keyword to
`app/core/vocabularies.py` before applying.

## Rolling back

```bash
python -m alembic downgrade c9d0e1f2a3b4       # drops typology_extra, description_clean, the geo codes
```

Reverting the code alone is also safe. The original wording of `property_type` / `typology` is in
`raw_payload` for rows scraped after this change; rows normalised by the backfill cannot be restored from
the DB, so take a backup (or `SELECT id, property_type, typology FROM listings` to CSV) before `--apply`.

## Geo dataset

`app/data/freguesias.csv` (UTF-8, header `dicofre,freguesia,concelho,distrito`; override with `CAOP_DATA_PATH`).
DICOFRE is `DDCCFF`, so one row per parish yields all three codes. Official parish codes are assigned by INE;
boundaries and names come from DGT's CAOP.

## Not done / limits

- The matching rules were tested on a miniature of the real table, not the full 3,259 parishes: run
  `backfill_geo_codes` without `--apply` and read the "county not matched" list (islands and renamed
  municipalities are the likely gaps).
- Description rules come from the examples in the brief; there is no real partner text in the repo, so
  validate `backfill_description_clean` on a sample and add per-partner patterns where needed.
- Existing AI translations are **not** regenerated when a listing's content changes. Re-run bulk enrichment
  with `force` for those listings.
