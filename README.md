# SpaceRank NYC

A commercial real-estate matching engine for New York City. A tenant describes
what they want (type, size, budget, area, free-text description) and the system
returns the best-fitting spaces — each with a 0-100 score, the reason it
surfaced, and a direct ownership-side leasing contact.

## Pipeline (run in this order)

```
python scrape_gfp.py         # 1a. scrape GFP Real Estate   -> gfp_listings.csv
python scrape_rudin.py       # 1b. scrape Rudin Management   -> rudin_listings.csv
python scrape_slgreen.py     # 1c. scrape SL Green           -> slgreen_listings.csv
python clean_dataset.py      # 2.  merge + PLUTO enrichment  -> spaces_clean.csv
python demo_match.py         # 3.  rank spaces for example tenant requests
python test_engine.py        # 4.  9 design-decision tests must pass
python -m uvicorn app:app    # 5.  full product at http://127.0.0.1:8000
```

Requires: `python -m pip install -r requirements.txt`
Optional (real semantic matching): `python -m pip install sentence-transformers`

## The files

| File | What it does |
|---|---|
| `scrape_gfp.py` | Scraper for gfpre.com. Hybrid: one call to their JSON API (`/api/property`) for all 59 buildings + 135 availabilities, then BeautifulSoup on each building page for the description + contacts (which ARE in the raw HTML — the availabilities table is not, it's JS-rendered from that API). |
| `gfp_listings.csv` | Raw scrape output. One row per available space; buildings with no availabilities keep one row with space fields blank. |
| `building_coords.json` | `slug -> [lng, lat]` for every building, taken from the same API (`mapbox_center`) — saved a whole geocoding step. |
| `clean_dataset.py` | Data engineering: parses `"$40.00 PSF"` -> `40.0` (never guesses when rent is "Upon request"), flags leased/placeholder rows, tags residential buildings, assigns boroughs, merges coordinates, and **joins NYC PLUTO by normalized address** (never by owner name — 95.2% of PLUTO owner strings own exactly one building). PLUTO supplies lat/lng where scrapers can't, plus year built / floors / building class. Address normalization handles PLUTO's format: "Eighth Avenue" -> "8 AVENUE", "53rd" -> "53", "One" -> "1". 57/101 buildings match; misses are mostly corner lots keyed by their other frontage (fix would be the NYC GeoSearch API — future work). |
| `spaces_clean.csv` | The engine's input. 457 rows across 3 landlords (101 buildings), 406 flagged `is_available`, enriched with PLUTO year built / floors / building class. |
| `semantic.py` | The "match by meaning" layer. Uses sentence-transformers embeddings + cosine similarity when installed; otherwise falls back to a from-scratch TF-IDF (keyword-weight) implementation so the pipeline always runs. |
| `matching.py` | The engine. Five signals, each 0..1: type, size, budget, geo (haversine distance to the requested area), semantic. Blended with explicit weights into one score. |
| `demo_match.py` | Three realistic tenant personas run end-to-end. |
| `scrape_rudin.py` | Scraper for rudin.com — the OPPOSITE architecture of GFP: fully server-rendered Drupal pages (paginated `/all-availabilities`), but no public emails (contact recorded honestly as the inquire-form link) and no rents. Building coordinates are embedded in each page (highest-precision pair = the map pin; the 6-decimal pair is their HQ footer map). |
| `landlord.py` | Layer 3 (v2) — ranks landlords on exactly THREE transparent signals (see 'How the landlord ranking works' below). No combined score is displayed; every number traces to real data. |
| `app.py` | FastAPI wrapper: `/api/match`, `/api/landlords`, `/api/areas`, auto-docs at `/docs`, serves the UI at `/`. |
| `static/index.html` | The tenant-facing single-file frontend: search form, ranked space cards with per-signal score bars, landlord panel, and a Leaflet results map with numbered pins. |
| `scrape_slgreen.py` | Scraper for slgreen.com (landlord #3, WordPress/Divi) — the richest source: 224 units with rent/term/occupancy, real @slgreen.com leasing contacts (C&W brokers filtered out per the ownership-side rule). Quirks: units render 3x (desktop/mobile/details) and must be deduped; ~35 of the 66 dropdown entries are external marketing sites and are skipped; no coordinates (PLUTO supplies them). |
| `tools/precompute_embeddings.py` | The offline half of the embedding backend: embeds every description with fp32 MiniLM, writes `embeddings.npz`, fetches the quantized ONNX export + tokenizer into `models/`. Run by CI or locally. |
| `.github/workflows/embeddings.yml` | GitHub Action: re-runs the precompute whenever `spaces_clean.csv` changes and commits the artifacts — which triggers the normal Vercel deploy. No self-trigger loop (the bot commit touches only artifact paths). |
| `test_engine.py` | 9 tests that pin down design decisions (neutral scores for unknowns, NaN-safe geo, descending order, landlord aggregation invariants). Run with `python test_engine.py` or pytest. |

## How the scoring works

```
score = 0.20*type + 0.20*size + 0.15*budget + 0.25*geo + 0.20*semantic
```

- **type** — exact use match 1.0, related use (Showroom~Retail) 0.6, mismatch 0.1
- **size** — 1.0 inside the requested range, decaying with % deviation outside
- **budget** — 1.0 within budget, decaying with % overage; *unknown rent = 0.5
  (neutral), because missing data should neither punish nor reward*
- **geo** — haversine (great-circle) distance from the space's coordinates to
  the requested neighborhood's centroid; 1.0 within 0.5 km, 0 beyond 8 km
- **semantic** — cosine similarity between the tenant's free text and the
  building descriptions. THREE backends, auto-selected and disclosed by the
  API (`semantic_backend`): (1) sentence-transformers MiniLM locally,
  (2) **the deployed path**: descriptions precomputed offline into
  `embeddings.npz` (~0.6 MB) by a GitHub Action, queries embedded at request
  time by the same MiniLM quantized to ONNX (~23 MB in `models/`,
  onnxruntime instead of PyTorch), (3) TF-IDF keyword overlap as last
  resort — labeled "NOT semantic". Why precompute instead of Render/Railway:
  zero migration and zero cost, faster requests (406 descriptions never
  re-embedded), and free-tier Render sleeps between requests + 512 MB RAM
  makes PyTorch painful. Tradeoff: embeddings refresh requires the Action
  run (automatic on data pushes) rather than happening implicitly.

Every result carries its per-signal scores and a reason string — a ranking you
can't explain is a ranking you can't defend.

## How the landlord ranking works (v2 — three transparent signals)

| signal | tenant label | what it is |
|---|---|---|
| `match_number` | **Spaces that fit you** | a COUNT: how many of the landlord's available spaces pass the tenant's HARD filters. Filter semantics: type strict; size strict (when a range is given); budget rejects only KNOWN rents above budget (92% of the market publishes "Upon request" — missing price ≠ bad price); area strict within 2 km, and an un-geocoded building FAILS (we never claim a location we can't verify). |
| `specialization` | **Area & type expertise** | `(X/Y) × (X/(X+5))` where X = their available spaces of the requested area+type, Y = all their available spaces. The percentage, damped by the absolute count — so a 3-of-3 boutique (0.38) doesn't beat a 101-of-208 giant (0.46). Percentage alone is never used. |
| `match_strength` | **Spaces match quality** | mean over their top-3 FITTING spaces of `0.25·size + 0.15·budget + 0.30·geo + 0.30·semantic`. The semantic part is cosine similarity between the tenant's free text and the descriptions (embeddings when sentence-transformers is installed, TF-IDF keyword overlap otherwise — the API reports which). The reason line quotes the matched phrase via `semantic.explain()`. If zero spaces fit, strength is `null` — reported, never faked. |

List ordering (internal only, returned as `ordering`, not displayed):
`0.40 · n/(n+10) + 0.25 · specialization + 0.35 · match_strength`.
The saturation `n/(n+10)` gives diminishing returns instead of an arbitrary cap.

## Design decisions worth remembering

1. **API over Playwright.** The availabilities table is empty in the raw HTML
   (JavaScript fills it). Instead of running a headless browser, we watched the
   network traffic, found `/api/property`, and got cleaner data in 1 request
   instead of ~60. Check the network tab before reaching for Playwright.
2. **Neutral scores for missing data.** 61% of rents are "Upon request";
   treating unknown as 0 would wrongly bury most of the inventory.
3. **Ranking, not prediction.** Scores rank real, countable signals. No
   invented "reliability" numbers with no ground truth.
4. **Contacts are ownership-side.** Primary contact per row is GFP's own
   Asset/Leasing Manager — never a broker. (Two buildings list outside
   agents — JLL at 1540 Broadway, Lee & Associates at 149 W 36th — flagged
   as a known caveat.)
5. **The scraped dataset is not republished.** It powers the app locally.

## Known data caveats

- Only 33 of 135 spaces have a numeric rent; the rest are "Upon request"/"Negotiable".
- 4 rows say "Leased" — kept in the CSV, excluded by `is_available`.
- 7 GFP properties are residential/other (SoMA, student housing, co-ops) — kept, tagged `building_use = residential/other`, excluded from matching.
- `address` = building name (street address); Jersey City / outer-borough rows carry it in `borough`.

## Roadmap position

Done: PLUTO backbone → GFP scraper → clean dataset → matching v1 (structured +
geo) → semantic layer (with fallback) → Rudin scraper (68 spaces) → landlord
ranking (Layer 3) → FastAPI backend + frontend → **DEPLOYED LIVE at
https://spacerank-nyc.vercel.app** → SL Green scraper (224 units — dataset now
406 available spaces / 3 landlords / 101 buildings) → PLUTO enrichment joined
by address → results map → test suite. (Scouted Silverstein too: their site
is a portfolio brochure with no availabilities or contacts — documented dead
end.) The deployed instance runs the TF-IDF semantic backend (embeddings are
too heavy for serverless). Next: install sentence-transformers locally for
real embeddings, more landlords (Vornado, Durst...). Bonus: price model.
