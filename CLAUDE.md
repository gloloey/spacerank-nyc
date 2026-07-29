# CLAUDE.md — SpaceRank NYC

Persistent instructions for Claude Code. HANDOFF.md and INTERVIEW_PREP.md
hold full project state and interview notes but are kept out of the public
repo (git-ignored, local-only) — check the working directory for them
before doing anything substantial.

## What this is
A commercial real-estate matching engine for NYC, live at
https://spacerank-nyc.vercel.app. A tenant describes what they need; the
system ranks every available space (0–100, five explained signals) and the
landlords behind them (three transparent signals). Built by Gabriel (student,
rusty Python) as a portfolio project for interviews.

## Non-negotiable project rules
1. **Every line must be simple enough to explain and defend from first
   principles.** Prefer simple, from-scratch, heavily-commented code over
   clever dependencies (the semantic fallback is hand-written TF-IDF; the
   price model is closed-form ridge in numpy — keep that spirit).
2. **Honest-data design principle.** Unknown values score neutral (0.5),
   display as "—"/"n/a" with an explanation — never as an invented number.
   Rent estimates are clearly marked, range-only, and never affect ranking,
   filtering, or counts (tests enforce this).
3. **Scrape politely, owner sites only.** Honest User-Agent, rate limits,
   ownership-side leasing contacts only — never brokers. The scraped
   dataset (including contact info) is published as part of this public
   repo and served by the live app — that's the product. Don't add data
   beyond what the owner sites themselves publish.
4. **No secrets in this repo.** A Google Maps browser key was committed by
   mistake once (since revoked) — see HANDOFF.md for the incident. If a
   feature needs a credential, Gabriel creates it himself; code reads it
   from an env var, never hardcoded. Before committing, check for API keys,
   tokens, or credentials in diffs — especially in `index.html`.
5. **The repo is public.** It's a portfolio piece — assume anything
   committed here is visible to recruiters and the internet. HANDOFF.md and
   INTERVIEW_PREP.md are the exception: git-ignored, local-only, since
   they're working notes rather than polished documentation.

## Architecture in one paragraph
`scrapers/scrape_*.py` (one per landlord, requests+BS4) → `data/raw/*_listings.csv` →
`clean_dataset.py` (merge, rent parsing, PLUTO enrichment via
`pluto_cache.json`, NYC GeoSearch geocoding via `geocode_cache.json`) →
`spaces_clean.csv` + `dataset_meta.json` → `matching.py` (5-signal engine)
+ `landlord.py` (3-signal landlord layer) + `semantic.py` (3-tier: local
sentence-transformers > ONNX MiniLM + precomputed `embeddings.npz` on
Vercel > from-scratch TF-IDF) + `price_model.py` (ridge rent estimates) →
`app.py` (FastAPI) → `index.html` (single-file frontend, no framework).
Deployed on Vercel via git integration (`vercel.json`, flat `index.py`
entrypoint).

## The automation (do not break the chain)
- `.github/workflows/refresh_data.yml` — Mondays 09:00 UTC + manual: runs
  every `scrapers/scrape_*.py`, rebuilds the dataset, retrains the price
  model, sanity-guards (>50% row collapse per landlord aborts), commits, then
  **explicitly dispatches** embeddings.yml (`gh workflow run`) because
  GITHUB_TOKEN pushes don't trigger on-push workflows.
- `.github/workflows/embeddings.yml` — re-embeds descriptions, commits
  `embeddings.npz` + `models/`, which triggers the Vercel deploy.
- Adding a landlord = write one `scrapers/scrape_<name>.py` (match the CSV
  schema in any existing scraper, write output to `data/raw/<name>_listings.csv`,
  include `image_url`), add it to `LANDLORD_PROFILES`
  in matching.py **only if** it honestly fits "institutional" (public
  REIT) or "family-run" (family-owned/led) — RXR fits neither and is
  intentionally left out, staying unclassified rather than forced. Commit;
  everything else is automatic.

## Commands
```
python -m pip install -r requirements.txt          # runtime deps
python -m pip install -r requirements-dev.txt      # + scraping/test deps
python test_engine.py            # 36 tests — MUST stay green (or: pytest)
python -m uvicorn app:app --reload                 # local server :8000
python clean_dataset.py          # rebuild dataset from data/raw/*_listings.csv
python price_model.py            # retrain rent model -> price_model.json
python demo_match.py             # 3 personas end-to-end in the terminal
```
Windows: use `python`, not `python3`.

## Conventions and hard-won gotchas
- **Every dataframe field emitted to JSON needs a NaN guard** (`_s()`/`_n()`
  in matching.py). NaN → "Out of range float values" → HTTP 500. This bug
  has happened three times; don't be the fourth.
- `index.html` and `static/index.html`: the repo has only the root
  `index.html` (app.py falls back to it). Don't create the static copy.
- `TenantRequest.__post_init__` sanitizes all nonsense input (budget≤0,
  swapped size bounds, unknown areas). Keep new inputs behind it.
- Vercel edge briefly caches `/` — cache-bust with `?v=N` when verifying.
- Frontend: single file, CSS variables, no build step, no framework,
  localStorage OK. `[data-tt]` = tooltip; `.ttd` variant opens downward.
- WordPress sites lazy-load images (src = 1×1 `data:` placeholder; real URL
  in `data-src`). The engine rejects `data:` URIs as images.
- `manhattan_pluto.csv` (23 MB) is deliberately NOT in the repo;
  `pluto_cache.json` is the committed snapshot that replaces it on machines
  that don't have it. Same pattern for geocoding (`geocode_cache.json`) and
  subway stations (`subway_stations.json`, built once by
  `tools/build_subway_stations.py` from NY State's open-data API — station
  coordinates don't drift, so this isn't part of the weekly refresh).
- Tests live in `test_engine.py` and pin DESIGN DECISIONS (neutral scores,
  term never ranked, estimates never ranked, count monotonicity). When you
  add a behavior rule, add a test that pins it.
- **Never regex-search a whole flattened page (`soup.get_text()`) for
  something that needs to be exact, like an address.** A scraper did this
  and a non-greedy match silently fused two different buildings' names
  from a site nav menu into one fake address, geocoding 6 buildings to the
  same wrong point undetected for weeks. Prefer a structured, isolated
  source instead — a specific `<meta>` tag, a JSON field, one DOM element
  — where cross-item contamination is structurally impossible.
- **Any new top-level import in `app.py`/`matching.py`/`landlord.py` needs
  a `requirements.txt` check**, not just "it works locally" — dev-only
  packages (`requirements-dev.txt`) are invisible locally but crash the
  live deployment on every route via `ModuleNotFoundError`. This has
  happened once already (`requests` for `/api/geocode`); after adding or
  changing any import, re-check both requirement files before pushing.

## Style
Python: stdlib + the pinned deps only; docstrings explain the WHY and the
site architecture for scrapers; comments teach (Gabriel reads them to learn).
Keep the honest-labels voice in UI copy ("Upon request", "Est.", "n/a").
