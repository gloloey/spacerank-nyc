# CLAUDE.md — SpaceRank NYC

Persistent instructions for Claude Code. Read HANDOFF.md for full project
state before doing anything substantial.

## What this is
A commercial real-estate matching engine for NYC, live at
https://spacerank-nyc.vercel.app. A tenant describes what they need; the
system ranks every available space (0–100, five explained signals) and the
landlords behind them (three transparent signals). Built by Gabriel (student,
rusty Python) as a portfolio project for interviews.

## Non-negotiable project rules
1. **Gabriel must be able to explain every line in an AI-free interview.**
   Prefer simple, from-scratch, heavily-commented code over clever
   dependencies (the semantic fallback is hand-written TF-IDF; the price
   model is closed-form ridge in numpy — keep that spirit).
2. **Never fake data.** Unknown values score neutral (0.5), display as
   "—"/"n/a" with an explanation — never as an invented number. Rent
   estimates are clearly marked, range-only, and NEVER affect ranking,
   filtering, or counts (tests enforce this).
3. **Scrape politely, owner sites only.** Honest User-Agent, rate limits,
   ownership-side leasing contacts only — never brokers. The scraped
   dataset is not republished elsewhere.
4. **No secrets in this repo.** There are none today (free APIs only). If a
   feature needs a credential (e.g. Vercel Postgres for leads), Gabriel
   creates it himself; code reads it from an env var.
5. **Keep the repo private.**

## Architecture in one paragraph
`scrape_*.py` (one per landlord, requests+BS4) → `*_listings.csv` →
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
  every `scrape_*.py`, rebuilds the dataset, retrains the price model,
  sanity-guards (>50% row collapse per landlord aborts), commits, then
  **explicitly dispatches** embeddings.yml (`gh workflow run`) because
  GITHUB_TOKEN pushes don't trigger on-push workflows.
- `.github/workflows/embeddings.yml` — re-embeds descriptions, commits
  `embeddings.npz` + `models/`, which triggers the Vercel deploy.
- Adding a landlord = write one `scrape_<name>.py` (match the CSV schema in
  any existing scraper, include `image_url`), add its style to
  `LANDLORD_PROFILES` in matching.py, commit. Everything else is automatic.

## Commands
```
python -m pip install -r requirements.txt          # runtime deps
python -m pip install -r requirements-dev.txt      # + scraping/test deps
python test_engine.py            # 28 tests — MUST stay green (or: pytest)
python -m uvicorn app:app --reload                 # local server :8000
python clean_dataset.py          # rebuild dataset from *_listings.csv
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
  that don't have it. Same pattern for geocoding (`geocode_cache.json`).
- Tests live in `test_engine.py` and pin DESIGN DECISIONS (neutral scores,
  term never ranked, estimates never ranked, count monotonicity). When you
  add a behavior rule, add a test that pins it.

## Style
Python: stdlib + the pinned deps only; docstrings explain the WHY and the
site architecture for scrapers; comments teach (Gabriel reads them to learn).
Keep the honest-labels voice in UI copy ("Upon request", "Est.", "n/a").
