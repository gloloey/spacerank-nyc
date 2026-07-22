# HANDOFF.md — SpaceRank NYC → Claude Code

Written 2026-07-20, updated 2026-07-21 (landlords #7–9, two new search
filters, a geocoding bug fix), updated again 2026-07-22 after a mass
landlord-expansion run (#10–17). Read CLAUDE.md first for the standing
rules; this file is the full state of the world.

## 0. Source of truth & git state (READ FIRST)

- **The GitHub repo `github.com/gloloey/spacerank-nyc` (private, branch
  `main`) is the single source of truth.** CI bots also commit to `main`
  (weekly data refresh + embeddings). If you are reading this inside a
  fresh `git clone`, there are no uncommitted changes — everything
  described below is committed.
- Files that exist only in an old local folder from before the first
  handoff, intentionally not in the repo: `manhattan_pluto.csv` (23 MB
  PLUTO backbone — replaced by the committed `pluto_cache.json`),
  `pluto_datadictionary.pdf`, `nyc_commercial_office_landlords.xlsx`,
  `phase1_inspect.py`, `static/index.html`, `api/`. None are needed.

## 1. Objective

A portfolio-grade commercial real-estate matching engine for NYC that
Gabriel (math+CS student) can defend line-by-line in AI-free interviews.
Tenant describes type/size/budget/areas/free-text (+ now: fit-out
condition and a custom "near a place" anchor) → ranked spaces + ranked
landlords with ownership-side contacts. Headline tech: semantic matching
via text embeddings; a from-scratch rent-estimation model. Live at
**https://spacerank-nyc.vercel.app**. Deadline: late August 2026.

## 2. What is completed (as of 2026-07-22, all verified live)

- **Seventeen landlord scrapers** (each documents its site's architecture
  in its docstring): GFP, Rudin, SL Green, Vornado, Durst, ESRT,
  Brookfield Properties, RXR Realty, Silverstein Properties (all as of
  the 2026-07-21 update above), plus this session's eight:
  **Paramount Group** (public JSON API behind their React SPA, exact
  per-suite floor/sqft), **Jack Resnick & Sons** (server-rendered, real
  remarks text, Cloudflare-obfuscated emails decoded by hand),
  **Sage Realty** (public WordPress REST API, embedded coords, filters
  JLL/CBRE brokers out of a mixed contact list), **The Feil Organization**
  (hidden admin-ajax JSON API with a rotating nonce, 13 NYC buildings),
  **Two Trees Management** (real Nestio CRM feed, office only — no
  structured retail data exists), **Time Equities** (server-rendered,
  caught a real $/SF-vs-$/month unit-confusion bug before it reached the
  dataset), **George Comfort & Sons** (single availabilities page —
  caught a malformed-HTML parser bug AND a whole-page content-duplication
  bug before trusting the output), **Brause Realty** (building-level only,
  caught a real geocoding bug where a marketing name collided with an
  unrelated real street name in a different borough).
- Researched and explicitly **rejected** as landlord targets (real site,
  confirmed no usable per-space data — not a scraping-difficulty issue):
  Tishman Speyer, Boston Properties (BXP), Related Companies, Fisher
  Brothers, Wharton Properties/Jeff Sutton, Savanna, Metro Loft,
  Rockefeller Group, Extell, Hines, Beacon Capital Partners (contacts are
  all CBRE/Cushman brokers — a hard-rule violation, not just low
  volume), American Realty Advisors, Kaufman Organization (its one
  working microsite's leasing contact is a Newmark broker), Slate
  Property Group, Taconic Partners, Thor Equities, Jamestown, Muss
  Development (its own "Availabilities" nav link points straight to
  LoopNet), Normandy Real Estate Partners (defunct as an independent
  landlord since a 2020 acquisition), Meadow Partners, Klein Enterprises
  (a same-named but unrelated Baltimore-area firm), Columbia Property
  Trust (investor-facing since going private in 2021, no leasing site).
- **Environment note for future sessions**: this sandbox sits behind an
  institutional network content filter (redirects to `filter.techloq.com`
  or a Squid error page) that blocks a meaningful fraction of smaller/
  newer real-estate domains somewhat unpredictably — confirmed
  domain-specific (not a general outage) by always testing a known-good
  control domain alongside the target. Landlords blocked this way and
  never actually seen: L&L Holding, RFR Realty, Nightingale Properties,
  Ashkenazy Acquisition, Trinity Real Estate, Cove Property Group, Global
  Holdings, Olayan America, Vanbarton Group, William Macklowe Company.
  These are **inconclusive, not rejected** — re-attempt from an unfiltered
  network before concluding anything about their actual sites.
- **Dataset**: 1123 rows / 1013 available spaces / 270 buildings / 17
  landlords. 100% coordinate coverage. 100% building-photo coverage
  (hotlinked; monogram fallback in UI for every landlord with no logo
  file on hand — most of them; only GFP and SL Green have one).
- **Matching engine** (`matching.py`): five signals (type .20, size .20,
  budget .15, geo .25, semantic .20) PLUS two small nudges — landlord
  style (+3, unchanged from before) and **fit-out condition** (+3, new:
  "turnkey"/"raw", parsed from listing description text by
  `clean_dataset.py:parse_fit_condition`, ~7% coverage, neutral for the
  rest). The geo signal now pools the tenant's chosen submarket areas
  AND an optional custom **anchor** point (a real subway station or a
  geocoded address) — closest candidate wins; `nearest_geo_target()` is
  the shared helper used by scoring, the live count filter, and the
  landlord layer alike.
- **"Near a place" search**: `subway_stations.json` is a committed
  snapshot of all 445 real NYC subway station complexes (name, lines,
  borough, lat/lng), built by `tools/build_subway_stations.py` from NY
  State's own open-data API (Socrata dataset `39hk-dx4f`, "MTA Subway
  Stations") — same one-time-fetch-then-cache pattern as
  `pluto_cache.json`/`geocode_cache.json`. `GET /api/subway-stations?q=`
  is a typeahead; `GET /api/geocode?q=` resolves a free-text address via
  NYC GeoSearch (same API the scrapers use). The frontend resolves either
  to a plain lat/lng/label CLIENT-side, then sends it as
  `anchor_lat`/`anchor_lng`/`anchor_label` — `/api/match` and `/api/count`
  never re-geocode per search.
- **Landlord layer** (`landlord.py`): unchanged three signals (match_number,
  specialization, match_strength); `_in_area` now also honors the anchor.
- **Semantic layer** (`semantic.py`): unchanged 3-tier backend.
- **Price model** (`price_model.py`): unchanged; new landlords contributed
  zero numeric rents so far, so it hasn't needed retraining.
- **API** (`app.py`, FastAPI): `/api/areas` (now also returns
  `fit_conditions`), `/api/match`, `/api/landlords`, `/api/count`,
  `POST /api/leads`, plus the two new endpoints above.
- **Frontend** (`index.html`): everything from before, plus a "Space
  condition" dropdown (step 3) and an "Or near a specific place" search
  box with typeahead + address fallback (step 2) — chip display, map
  marker (📍), shareable-URL round-trip, live-count hard-filter,
  distance badge on every result card.
- **Automation**: unchanged weekly self-refresh workflow. Note: this
  session's commits were pushed directly (not through the refresh
  workflow), so `embeddings.yml` was NOT auto-dispatched for the new
  landlords' descriptions yet — the next scheduled Monday refresh (or a
  manual `gh workflow run embeddings.yml`) will pick them up for semantic
  search. They already rank/filter/geocode correctly in the meantime;
  only the "vibe" semantic-match signal is affected (falls back to the
  TF-IDF/neutral path for descriptions not yet embedded).
- **Tests** (`test_engine.py`): **33**, all green (5 new: anchor
  sanitization, anchor pooling with areas, anchor as a count hard-filter,
  fit-preference nudge, subway station search; 1 extended: count ignores
  ranking-only inputs now also covers `fit_preference`; 1 fixed: the
  landlord-roster test was comparing against `LANDLORD_PROFILES`, which
  intentionally omits RXR — now compares against the actual dataset).

## 3. Important decisions and why (the interview answers)

- **Hidden APIs / server-rendered HTML / public but "undocumented" JSON
  APIs over headless browsers**: check the network tab (or curl) first —
  GFP took 1 request instead of 60; Brookfield's Algolia index and
  Silverstein's Directus API are the same idea one level further.
- **Neutral 0.5 for missing data** (now also true for fit-out condition
  and the geo anchor): the same "never invent, never penalize unknown"
  rule extends to every new signal, not just the original five.
- **Never trust a regex over a whole flattened page for structured data
  like an address.** `scrape_durst.py` originally did exactly that and
  silently fused two different buildings' names from a site-wide nav
  menu into one fake address for 6 buildings, which all then geocoded to
  the same wrong point (found while adding the proximity-search feature,
  which made the bug visible for the first time). Fixed by reading the
  page's own `<meta name="keywords">` tag instead (page-specific,
  comma-isolated, so cross-item contamination is structurally
  impossible). **Lesson for future scrapers**: prefer a structured,
  isolated source (a meta tag, a JSON field, a specific DOM element) over
  `soup.get_text()` search whenever the target is something exact like an
  address, not prose.
- **`requirements.txt` vs `requirements-dev.txt` must be kept in sync
  with what `app.py` actually imports at runtime.** Adding `/api/geocode`
  (which imports `requests`) broke the LIVE deployment with
  `ModuleNotFoundError` on every single route, not just that one,
  because `requests` was only listed as a scraper/dev dependency.
  **Lesson**: any new top-level import in `app.py`/`matching.py`/
  `landlord.py` needs a `requirements.txt` check, not just a "does it
  work locally" check (locally, dev deps are already installed, so this
  class of bug is invisible until deploy).
- **A custom geo-anchor pools with areas rather than replacing them**:
  `nearest_geo_target()` treats the areas list and the anchor as one
  combined candidate set — closest wins — mirroring how multiple areas
  already worked. Simpler than a separate "OR" toggle in the UI.
- Everything from the previous handoff still applies (PLUTO joined by
  address never owner name; embeddings precomputed in CI + ONNX at
  runtime; landlord ranking as three separate signals; ridge + LOO CV for
  the price model; `GITHUB_TOKEN` pushes don't trigger workflows; the
  `*_cache.json` snapshot pattern, now including `subway_stations.json`).

## 4. File map

| File | Role |
|---|---|
| `scrape_gfp.py` / `scrape_rudin.py` / `scrape_slgreen.py` / `scrape_vornado.py` / `scrape_durst.py` / `scrape_esrt.py` / `scrape_brookfield.py` / `scrape_rxr.py` / `scrape_silverstein.py` / `scrape_paramount.py` / `scrape_resnick.py` / `scrape_sage.py` / `scrape_feil.py` / `scrape_twotrees.py` / `scrape_timeequities.py` / `scrape_gcomfort.py` / `scrape_brause.py` | one scraper per landlord (17 total) → `*_listings.csv` |
| `clean_dataset.py` | merge, rent parsing, fit-condition parsing, PLUTO enrichment, geocoding → `spaces_clean.csv`, `dataset_meta.json`, caches |
| `matching.py` | 5-signal engine + geo-anchor pooling + fit-condition nudge + `count_spaces` + NaN guards + `LANDLORD_PROFILES`/`AREAS`/`SUBWAY_STATIONS` |
| `landlord.py` | 3-signal landlord ranking (anchor-aware) |
| `semantic.py` | 3-tier text-matching backend |
| `price_model.py` | ridge rent estimator → `price_model.json` |
| `app.py` / `index.py` | FastAPI app / flat Vercel entrypoint |
| `index.html` | the entire frontend |
| `test_engine.py` | 33 design-decision tests |
| `tools/precompute_embeddings.py` | offline embedding pipeline (CI) |
| `tools/build_subway_stations.py` | one-time fetch of the real MTA station list → `subway_stations.json` |
| `.github/workflows/refresh_data.yml` / `embeddings.yml` | the automation chain |
| `vercel.json` | build config — `includeFiles` must list every file the serverless function reads (now includes `subway_stations.json`) |
| `spaces_clean.csv`, `dataset_meta.json`, `embeddings.npz`, `models/`, `price_model.json`, `pluto_cache.json`, `geocode_cache.json`, `building_images.json`, `building_coords.json`, `subway_stations.json` | data + committed artifacts |
| `INTERVIEW_PREP.md` | interview Q&A prep doc (done — see §8, this was the prior handoff's top priority) |

## 5. Run it

```
python -m pip install -r requirements.txt       # fastapi, uvicorn, pandas, numpy, requests, onnxruntime, tokenizers
python -m pip install -r requirements-dev.txt   # + requests, bs4, lxml, httpx (tests), sentence-transformers (optional)
python test_engine.py                           # 33/33 expected
python -m uvicorn app:app --reload              # http://127.0.0.1:8000
```
Deployment is automatic: any push to `main` deploys via Vercel git
integration. Data refresh: Actions → refresh-data → Run workflow (or wait
for Monday 09:00 UTC).

## 6. Tests performed (results)

`python test_engine.py` → **33 passed**. Everything from the prior
handoff (engine invariants, multi-area, style-nudge cap, term never
scored, no duplicates, deep ranking, count monotonicity, lead
validation, estimates never touch ranking, model self-gates, freshness
meta shape) PLUS: anchor sanitizes bogus/off-map input to `None`; anchor
pools correctly with areas in `score_geo`/`nearest_geo_target`; anchor
acts as a hard filter for the live count (like areas, unlike style/term/
fit); fit-preference is a nudge (+3 max, never a driver) with neutral
treatment for unknown/mismatched condition; real subway-station search
returns nothing for short queries and finds known stations. Live
verification after this session: `/api/subway-stations`,
`/api/geocode`, and a combined anchor+fit `/api/match` call all checked
against the running server; all 11 Durst buildings confirmed at
distinct, geographically-correct coordinates post-fix; production
deployment hit a `ModuleNotFoundError: requests` 500 immediately after
first deploy (see §3) — fixed and reconfirmed live via direct curl to
`/`, `/api/areas`, `/api/subway-stations`.

## 7. Known bugs / limitations / open questions

- **Leads are ephemeral** (Vercel function logs). Durable storage needs
  Gabriel to create Vercel Postgres/KV (credential is his); then wire
  `POST /api/leads` to it (~20 lines) keeping the log as fallback.
- **Price model trains on GFP only** (32 rents, $39–58 range) — none of
  the 3 newest landlords have published numeric rents yet, so this is
  unchanged; coverage grows automatically as data accrues.
- **Building grouping only collapses CONSECUTIVE same-building rows.**
- **RXR coverage is intentionally thin** (2 of ~32 NYC properties) — see
  `scrape_rxr.py`'s docstring; extending it means hand-verifying more
  per-building microsites one at a time, not writing a crawler.
- **Silverstein is building-level only** (no per-suite table exists on
  their API) — one row per building, `floor_suite` is a generic
  "Available — contact for floor plans" unless the floor-plate text
  happens to be a single unambiguous number.
- **Fit-out condition coverage is low (~7% of listings)** — by design,
  never inferred beyond what the listing text actually says.
- **245 Park Avenue (SLG)** redirects to an external site — not scraped.
- Some ESRT assets are Westchester/CT — kept without NYC coords, so area
  filters honestly exclude them.
- Hotlinked images/logos depend on landlord sites (UI has monogram
  fallbacks by design); Brookfield/RXR/Silverstein have no logo file yet
  (same monogram-fallback treatment as Rudin/Vornado/Durst/ESRT).
- **New landlords' descriptions aren't embedded for semantic search yet**
  (see §2 automation note) until the next scheduled/manual embeddings run.
- Gabriel's Phase-6 comprehension quiz (from the original GFP build) was
  never answered — fold into interview prep.

## 8. Remaining work, by priority

1. **Durable lead storage** — blocked on Gabriel creating Vercel
   Postgres/KV; then implement the write path + a small `/api/leads` GET
   for himself, keep log fallback, add tests.
2. **Landlord #18+** — 38 candidates researched across this session and
   the prior one; see §2 for the full rejected list (real sites, no
   usable data) and the network-blocked list (inconclusive — retry from
   an unfiltered network first). Remaining untried ideas worth a look:
   The Klein Group (kleingroupcre.com — NOT the same as the
   Baltimore-area "Klein Enterprises" already ruled out), Silverstein's
   or Feil's still-uncovered secondary buildings, or any of the
   network-blocked names above once reachable. Verify each by hand before
   writing a scraper — same standard as this session's work — and expect
   a real hit rate around 20-25% of candidates even when reachable; most
   NYC landlords simply don't publish per-space data on their own domain.
3. **Custom domain + Vercel Analytics** — dashboard actions (Gabriel).
4. **Polish**: non-consecutive building grouping, ESRT retail typing,
   mobile/Lighthouse pass, per-suite descriptions for SLG, extend the
   RXR microsite registry and Two Trees' retail coverage if more can be
   hand-verified live.

## 9. Exact next step for Claude Code

Run the test suite to validate the environment:
```
python -m pip install -r requirements.txt -r requirements-dev.txt
python test_engine.py        # expect 33 passed
```
`embeddings.yml` auto-triggers on every push that touches `spaces_clean.csv`
(confirmed directly — no manual dispatch needed, unlike what an earlier
version of this note assumed). Item 1 (durable leads) is blocked on
Gabriel; item 2 (more landlords) is the best uncredentialed next step —
research candidate sites for real per-space availability data BEFORE
writing a scraper, verify with direct HTTP requests (not just WebFetch
summaries) for anything address/image/contact-critical, spot-check the
scraper's OWN output (coordinates, floor numbers, rent units) rather than
trusting a clean-looking recon report, and add a test for any new
behavior rule.
