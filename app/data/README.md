# freguesias.csv

Lookup table used by `app/services/geo_normalizer.py` to turn free-text district /
county / parish into official codes (DICOFRE: 2 digits district, 4 municipality, 6 parish).

Columns: `dicofre`, `freguesia`, `concelho`, `distrito` (the columns the loader reads).

**Source:** Aldeias Portuguesas (2026). *Dataset de freguesias de Portugal*, v2026.2,
`core.csv` — https://aldeiasportuguesas.pt/dados/ (accessed 2026-10-20). Names from DGAL,
boundaries from DGT's CAOP; DICOFRE codes assigned by INE.
**Licence:** CC BY-SA 4.0 (attribution + share-alike). Reduced here to the four columns above.

Notes: Madeira and the Azores appear as islands in `distrito` ("Ilha da Madeira", ...), as in
DICOFRE. Rows whose code is not numeric (restored parishes, provisional codes) are kept in the
file but ignored by the loader. To refresh, download a newer `core.csv` and rewrite this file.
