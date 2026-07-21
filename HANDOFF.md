# HANDOFF.md — SpaceRank NYC → Claude Code

Written 2026-07-20 after direct inspection of the working tree and the live
deployment. Read CLAUDE.md first for the standing rules; this file is the
full state of the world.

## 0. Source of truth & git state (READ FIRST)

- **The GitHub repo `github.com/gloloey/spacerank-nyc` (private, branch
  `main`) is the single source of truth.** Every change so far was pushed
  through the GitHub web UI; CI bots also commit to `main`.
- The old working folder used before this handoff was **not a git repo** and
  its data files are **stale** (457-row / 3-landlord snapshot vs. the repo's
  current 618-available / 6-landlord dataset, which CI refreshes weekly).
  If you are reading this inside a fresh `git clone`, you are in the right
  place and there are **no uncommitted changes** — everything described
  below is committed on `main`.
- Files that exist only in the old local folder, intentionally not in the
  repo: `manhattan_pluto.csv` (23 MB PLUTO backbone — replaced by the
  committed `pluto_cache.json`), `pluto_datadictionary.pdf`,
  `nyc_commercial_office_landlords.xlsx` (early research),
  `phase1_inspect.py` (learning scratch), `static/index.html` (redundant
  copy; the app serves root `index.html`), `api/` (obsolete early Vercel
  layout). None are needed. `clean_dataset.py` runs fine without the PLUTO
  csv (falls back to `pluto_cache.json` and prints a note).

## 1. Objective

A portfolio-grade commercial real-estate matching engine for NYC that
Gabriel (math+CS student) can defend line-by-line in AI-free interviews.
Tenant describes type/size/budget/areas/free-text → ranked spaces + ranked
landlords with ownership-side contacts. Headline tech: semantic matching via
text embeddings; a from-scratch rent-estimation model. Live at
**https://spacerank-nyc.vercel.app**. Deadline: late August 2026.

## 2. What is completed (v0.11.2, all verified live)

- **Six landlord scrapers** (each documents its site's architecture in its
  docstring): GFP (hidden JSON API + BS4), Rudin (server-rendered Drupal),
  SL Green (WordPress/Divi, 3×-rendered rows deduped), Vornado
  (server-rendered portfolio pages, embedded coords, NYC bbox filter),
  Durst (single availabilities page, per-suite COMMENTS descriptions),
  ESRT (WordPress cards, lazy-load image gotcha handled).
- **Dataset**: ~708 rows / ~618 available spaces / 116 buildings with
  availability / 6 landlords. 100% coordinate coverage (PLUTO join by
  normalized address + NYC GeoSearch geocoding with committed cache).
  100% building-photo coverage (hotlinked from landlord sites;
  `building_images.json` for the original three, scraper-harvested
  `image_url` column for the new three; monogram fallback in UI).
- **Matching engine** (`matching.py`): five signals (type .20, size .20,
  budget .15, geo .25, semantic .20), neutral 0.5 for unknowns, per-result
  reasons, +3-point landlord-style tiebreaker, `term` stored-never-scored,
  NaN-guarded JSON, `count_spaces()` for the live count preview.
- **Landlord layer** (`landlord.py`): exactly three transparent signals —
  match_number (hard-filter COUNT), specialization ((X/Y)·(X/(X+5))),
  match_strength (top-3 mean, None when nothing fits). No combined score.
- **Semantic layer** (`semantic.py`): 3-tier — local sentence-transformers >
  ONNX MiniLM + precomputed `embeddings.npz` (the deployed path) >
  from-scratch TF-IDF labeled "NOT semantic". Proven meaning-match:
  "bright sunlit" surfaces "excellent natural light".
- **Price model** (`price_model.py`): from-scratch numpy ridge
  (closed form), leave-one-out CV λ selection, LOO-residual 10–90% bands,
  training-envelope gating, self-refuses below 25 rents or 30% rel. MAE.
  Current model: n=32 (all GFP office), LOO MAE ≈ $3.2/SF. Estimates are
  display-only — never ranked/filtered/counted (test-enforced).
- **API** (`app.py`, FastAPI): `/api/areas` (submarkets, styles, dataset
  freshness meta, price-model card), `/api/match`, `/api/landlords`,
  `/api/count` (hard-filter preview), `POST /api/leads` (Pydantic-validated,
  logged as structured `SPACERANK_LEAD` lines to Vercel logs — honestly
  ephemeral; durable DB is a known next step needing a credential).
- **Frontend** (`index.html`, single file, no framework): hero + 3 persona
  presets, guided 4-step filter rail, live count on the Search button,
  score rings, honest signal bars ("—"/"n/a" + tooltips instead of fake
  50%), landlord cards with ⓘ formula explainers + top-3 fits, dual-mode
  map (Leaflet+Carto transit tiles now; Google Maps + TransitLayer if a key
  is pasted into `GMAPS_KEY`), favorites drawer, dark mode, shareable
  search URLs, lead modal, freshness footer, rent-estimate chips (≈).
- **Automation**: weekly self-refresh workflow → scrape all → clean →
  geocode → retrain price model → sanity guard → commit → dispatch
  embeddings workflow → Vercel deploy. Proven end-to-end six times.
- **Tests** (`test_engine.py`): 28, all green, pinning design decisions.

## 3. Important decisions and why (the interview answers)

- **Hidden APIs / server-rendered HTML over headless browsers**: check the
  network tab first; GFP took 1 request instead of 60.
- **Neutral 0.5 for missing data**: 92% of rents are unpublished; scoring
  unknown as bad would bury the market. UI shows "—"/"n/a", never 50%.
- **PLUTO joined by address, never owner name** (95.2% of owner strings own
  exactly one building — name-matching would be wrong and unverifiable).
- **Embeddings precomputed in CI + ONNX at runtime** instead of moving to
  Render/Railway: zero cost, zero migration, faster requests; tradeoff is
  refresh-on-commit, which the pipeline automates anyway.
- **Landlord ranking = three separate signals, no combined %**: every
  number traces to countable facts; ordering weights are internal only.
- **Ridge + LOO CV for the price model**: n≈32 → anything fancier memorizes
  noise; LOO is the right CV at that size. Landlord identity excluded from
  features (leakage). Envelope gating stops extrapolation (a GFP-loft model
  must not price the Empire State Building). Band = real LOO residuals.
- **GITHUB_TOKEN pushes don't trigger workflows** → the refresh workflow
  explicitly dispatches embeddings.yml. Do not "simplify" this away.
- **`pluto_cache.json` / `geocode_cache.json` pattern**: big or
  network-dependent resources get a small committed snapshot so CI runners
  and fresh clones work identically.

## 4. File map

| File | Role |
|---|---|
| `scrape_gfp.py` / `scrape_rudin.py` / `scrape_slgreen.py` / `scrape_vornado.py` / `scrape_durst.py` / `scrape_esrt.py` | one scraper per landlord → `*_listings.csv` |
| `clean_dataset.py` | merge, rent parsing, PLUTO enrichment, geocoding → `spaces_clean.csv`, `dataset_meta.json`, caches |
| `matching.py` | 5-signal engine + `count_spaces` + NaN guards + `LANDLORD_PROFILES`/`AREAS` |
| `landlord.py` | 3-signal landlord ranking |
| `semantic.py` | 3-tier text-matching backend |
| `price_model.py` | ridge rent estimator → `price_model.json` |
| `app.py` / `index.py` | FastAPI app / flat Vercel entrypoint |
| `index.html` | the entire frontend |
| `test_engine.py` | 28 design-decision tests |
| `tools/precompute_embeddings.py` | offline embedding pipeline (CI) |
| `.github/workflows/refresh_data.yml` / `embeddings.yml` | the automation chain |
| `vercel.json` | build config — `includeFiles` must list every file the serverless function reads |
| `spaces_clean.csv`, `dataset_meta.json`, `embeddings.npz`, `models/`, `price_model.json`, `pluto_cache.json`, `geocode_cache.json`, `building_images.json`, `building_coords.json` | data + committed artifacts (CI-maintained) |

## 5. Run it

```
python -m pip install -r requirements.txt       # fastapi, uvicorn, pandas, numpy, onnxruntime, tokenizers
python -m pip install -r requirements-dev.txt   # requests, bs4, lxml, httpx (tests), sentence-transformers (optional)
python test_engine.py                           # 28/28 expected
python -m uvicorn app:app --reload              # http://127.0.0.1:8000
```
Deployment is automatic: any push to `main` deploys via Vercel git
integration. Data refresh: Actions → refresh-data → Run workflow (or wait
for Monday 09:00 UTC).

## 6. Tests performed (results)

`python test_engine.py` → **28 passed** (engine invariants, multi-area,
style-nudge cap ≤3.01, term never scored, no duplicates, deep ranking ≥400,
count monotonicity, count ignores ranking-only inputs, count endpoint ==
engine, lead validation 201/422 + payload bounding, estimates never change
scores/order/counts, estimate sanity 5≤low≤psf≤high≤400, model self-gates
on tiny data, freshness meta shape). Live verification after every version:
search flows, personas, URL restore, map markers, photo coverage 100%
(sampled 500 results), lead POST (real ref ids in Vercel logs), semantic
proof query, count shrink 377→36 under filters.

## 7. Known bugs / limitations / open questions

- **Leads are ephemeral** (Vercel function logs). Durable storage needs
  Gabriel to create Vercel Postgres/KV (credential is his); then wire
  `POST /api/leads` to it (~20 lines) keeping the log as fallback.
- **Price model trains on GFP only** (32 rents, $39–58 range) — the low MAE
  partly reflects the tight range; envelope gating keeps it honest (~10
  estimates ship). Durst occasionally publishes numeric rents; coverage
  grows automatically as data accrues.
- **Building grouping only collapses CONSECUTIVE same-building rows**; a
  building whose suites rank non-consecutively renders as two cards. Minor.
- **Vornado/Durst/ESRT have no logo files** in the frontend `LOGOS` map →
  monogram chips (Rudin too; its logo is CSS-drawn on their site).
- **245 Park Avenue (SLG)** redirects to an external site — not scraped.
- Some ESRT assets are Westchester/CT — kept without NYC coords, so area
  filters honestly exclude them.
- Hotlinked images/logos depend on landlord sites (UI has monogram
  fallbacks by design).
- GitHub web-UI quirks (only relevant if ever editing via browser again):
  commit buttons sometimes swallow clicks — `form.requestSubmit()` works.
- Gabriel's Phase-6 comprehension quiz (from the original GFP build) was
  never answered — fold into interview prep.

## 8. Remaining work, by priority

1. **Durable lead storage** — blocked on Gabriel creating Vercel
   Postgres/KV; then implement the write path + a small `/api/leads` GET
   for himself (auth via the same env var), keep log fallback, add tests.
2. **Interview prep doc** — 10 likely questions with answers in the
   project's own terms (why ridge/LOO, why neutral 0.5, why no combined
   landlord score, the MAE caveat), plus the unanswered quiz. Arguably the
   highest-value remaining task given the late-August deadline.
3. **Google Maps key** — Gabriel pastes into `GMAPS_KEY` in index.html
   (marked constant; instructions in the comment above it).
4. **Landlord #7+** (Brookfield, RXR, Tishman Speyer publish
   availabilities) — one scraper file each; pipeline handles the rest.
5. **Custom domain + Vercel Analytics** — dashboard actions (Gabriel).
6. **Polish**: non-consecutive building grouping, ESRT retail typing,
   mobile/Lighthouse pass, per-suite descriptions for SLG.

## 9. Exact next step for Claude Code

Run the test suite to validate the environment, then start item 2
(interview prep doc), which needs no credentials:

```
python -m pip install -r requirements.txt -r requirements-dev.txt
python test_engine.py        # expect 28 passed
```

Then create `INTERVIEW_PREP.md` covering: the 60-second project pitch; how
each scraper works and why that approach; the five signals and their
weights; neutral-scoring philosophy; the landlord three-signal design; the
embedding pipeline (and the TF-IDF fallback he wrote from scratch); the
price model end-to-end including its caveats; the CI chain; and the
Phase-6 quiz questions with worked answers. Keep every explanation tied to
actual file/line references so Gabriel can rehearse against the real code.
