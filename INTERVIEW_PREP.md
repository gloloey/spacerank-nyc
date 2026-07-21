# INTERVIEW_PREP.md — SpaceRank NYC

Rehearsal notes for Gabriel. Every claim below points at the real file/line
so you can pull the source up live and walk through it. Written 2026-07-21.

**One gap flagged up front:** HANDOFF.md references a "Phase-6 comprehension
quiz... from the original GFP build" that was never answered. I searched the
current repo, its full git history, and the old local working folder and
could not find that quiz anywhere — it isn't checked into either place, so
it's likely something that existed only in a past chat session that wasn't
saved to a file. I didn't fabricate a replacement claiming to be it. Section
9 below is a **practice quiz I wrote myself**, covering the same territory
(GFP + the engine), clearly labeled as mine, not a recovery of the original.

---

## 1. The 60-second pitch

"SpaceRank NYC ranks commercial real estate for tenants the way a good
broker would, but transparently. A tenant describes what they need — space
type, size, budget, neighborhood, and a free-text wish like 'bright space
near transit' — and the engine scores every available listing from six NYC
landlords on five explainable signals, 0–100, with a plain-English reason
per result. It also ranks the *landlords* themselves on three separate,
countable signals — never a fuzzy blended score. Two pieces I built from
scratch to actually understand them end-to-end rather than call a library
blindly: a hand-written TF-IDF semantic-search fallback, and a closed-form
ridge regression rent estimator with leave-one-out cross-validation. The
whole pipeline — six scrapers, data cleaning, PLUTO/geocoding enrichment,
embedding refresh, price-model retraining — runs unattended every week via
GitHub Actions and deploys to Vercel. It's live at spacerank-nyc.vercel.app."

Follow-up if asked "why does this matter": commercial listings sites hide
their ranking logic and often don't explain *why* a result is a good fit.
This one shows its work, and it never invents a number it doesn't have.

---

## 2. The six scrapers — one architecture each, on purpose

The project deliberately picked landlords with **different site
architectures** so the scraper layer teaches (and can defend) more than one
technique. Every scraper opens with a docstring documenting exactly how its
site works — that's the file to have open if asked "how did you find this."

| Landlord | File | Architecture | Key trick |
|---|---|---|---|
| GFP Real Estate | [scrape_gfp.py](scrape_gfp.py) | Hidden JSON API (`/api/property`) feeds a client-rendered table; HTML is server-rendered for description + contacts | 1 API call gets all ~59 buildings' availabilities instead of scraping 60 pages — found by checking the Network tab first ([scrape_gfp.py:7-15](scrape_gfp.py#L7-L15)) |
| Rudin Management | [scrape_rudin.py](scrape_rudin.py) | Fully server-rendered Drupal, paginated `/all-availabilities` | No public leasing emails exist — the contact is honestly recorded as the inquiry form link, never invented ([scrape_rudin.py:8-11](scrape_rudin.py#L8-L11)) |
| SL Green | [scrape_slgreen.py](scrape_slgreen.py) | WordPress/Divi, rich per-unit tables, real `@slgreen.com` emails, no coordinates | Each unit table renders **three times** (desktop/mobile/expanded) — must dedupe by keeping named-suite rows first, then unseen blank-suite rows ([scrape_slgreen.py:17-20](scrape_slgreen.py#L17-L20)); coordinates come later from the PLUTO join |
| Vornado Realty Trust | [scrape_vornado.py](scrape_vornado.py) | Server-rendered landing pages, coordinates embedded as `data-latitude`/`data-longitude` | Portfolio is national (555 California, THE MART) — filtered to NYC via a hard lat/lng bounding box ([scrape_vornado.py:16-18](scrape_vornado.py#L16-L18),[35](scrape_vornado.py#L35)) |
| The Durst Organization | [scrape_durst.py](scrape_durst.py) | One page, `/availabilities`, grouped by building | The `COMMENTS` column is a genuine **per-suite** description — rare; most landlords only describe at the building level ([scrape_durst.py:8-11](scrape_durst.py#L8-L11)) |
| Empire State Realty Trust | [scrape_esrt.py](scrape_esrt.py) | WordPress cards on `/leasing/` | The `wp-json` REST API returns 401 (tried it, it's blocked) so the rendered page is parsed instead; images lazy-load via a 1×1 `data:` placeholder with the real URL in `data-src` — the engine explicitly rejects `data:` URIs as images ([scrape_esrt.py:14-15](scrape_esrt.py#L14-L15), guarded in [matching.py:369](matching.py#L369)) |

**Common thread across all six** (the interview-defensible principle):
check the network tab / view-source *before* reaching for a headless
browser. GFP alone turned a potential ~60-request Selenium job into 1 API
call + ~59 lightweight GETs. Every scraper sends an honest, project-tagged
User-Agent (e.g. [scrape_gfp.py:38-41](scrape_gfp.py#L38-L41)), rate-limits
itself, and only records ownership-side contacts — brokers are filtered out
explicitly (e.g. SL Green mixes in Cushman & Wakefield brokers and keeps
only `@slgreen.com` addresses, [scrape_slgreen.py:21-22](scrape_slgreen.py#L21-L22)).

**If asked "why not just use Selenium/Playwright for everything":** slower,
heavier, and unnecessary — 5 of 6 sites are server-rendered HTML that
`requests` + BeautifulSoup handles directly; only GFP needed the API
shortcut, which is *less* work than a headless browser, not more.

---

## 3. The five matching signals ([matching.py](matching.py))

```
WEIGHTS = {"type": 0.20, "size": 0.20, "budget": 0.15, "geo": 0.25, "semantic": 0.20}
```
[matching.py:44](matching.py#L44) — one dict, one place, visible in the docstring too.

| Signal | Function | What 1.0 / 0.5 / low means |
|---|---|---|
| type | [score_type](matching.py#L169-L181) | exact use match = 1.0; "related" use (e.g. Retail asked, Showroom listed) = 0.6; mismatch = 0.1; unlisted = 0.5 |
| size | [score_size](matching.py#L184-L196) | 1.0 inside the tenant's [min,max] sqft range; decays linearly with % deviation outside it; unlisted or no target = 0.5 |
| budget | [score_budget](matching.py#L199-L209) | 1.0 at or under budget; decays as `1 - 2*overage_pct`; unknown rent ("Upon request") = 0.5, **never punished** |
| geo | [score_geo](matching.py#L234-L243) | haversine distance to the **nearest** of the tenant's selected submarkets (they can pick several); 1.0 within 0.5 km, fades to 0 by 8 km; no area picked or ungeocoded = 0.5 |
| semantic | [semantic.similarity](semantic.py#L156-L181) | cosine similarity in [0,1] between the tenant's free text and the listing description, via whichever backend is live (§6) |

Final score: `100 * Σ(weight_i * signal_i)`, plus an optional small
**landlord-style nudge** (+3 points flat, capped at 100) if the tenant
stated an institutional/family-run preference and it matches — deliberately
tiny so it can never flip a ranking, only break a near-tie
([matching.py:44-46](matching.py#L44-L46), [347-352](matching.py#L347-L352)).
`term` ("short"/"long" lease) is captured and stored but has **zero** effect
on any score — it's future landlord-contact metadata, and
`test_term_is_stored_but_never_scored` pins that.

Every result carries its per-signal breakdown (`signals` dict) and a
human-readable `reason` string built from the same scorer functions'
"reason fragment" return values — the UI shows exactly what the number
means, not just the number.

---

## 4. Why neutral (0.5), never a penalty, for unknown data

**The design decision, stated plainly:** 92% of NYC commercial listings
publish "Upon request" instead of a rent
([price_model.py:4](price_model.py#L4)). If the engine scored an unknown
rent as 0 (bad), it would systematically bury a huge share of the honest
market underneath spaces that simply chose to publish a number — punishing
transparency about pricing is backwards. So every scorer treats "we don't
know" as **neutral**, not negative: `score_budget(NaN, budget) == 0.5`
([matching.py:203-204](matching.py#L203-L204)), same pattern for size, geo,
and type. The UI never shows a bar at 50% and calls it a real number either
— it renders "—" / "n/a" with a tooltip explaining *why* it's unknown, so a
neutral score is never mistaken for a middling one.

This is also why the CLAUDE.md project rule exists: "unknown values score
neutral, display as '—'/'n/a', never as an invented number" — and why the
rent-estimate feature (§7) is display-only and excluded from every score,
filter, and count by construction, with a test enforcing it
(`test_estimates_never_touch_ranking_or_count`,
[test_engine.py:282-300](test_engine.py#L282-L300)).

If pressed "isn't 0.5 arbitrary": yes, and that's the point — it's the
*exact midpoint* of the [0,1] scoring range, so an unknown signal has
precisely zero directional pull on the final weighted sum, by construction,
not by tuning.

---

## 5. The landlord layer's three-signal design ([landlord.py](landlord.py))

Deliberately **no combined percentage** is ever shown for a landlord — only
three signals a tenant can verify by counting:

1. **`match_number`** — literal COUNT of the landlord's available spaces
   that pass the tenant's *hard* filters (type strict, size strict when
   given, budget rejects only known-and-over rents, area strict within 2 km)
   — [passes_hard_filters](landlord.py#L54-L64). It's an integer, not a
   ratio, so "12 fitting spaces" means exactly that.
2. **`specialization`** — `(x/y) * (x/(x+5))`: the share of the landlord's
   *entire* portfolio that's in the tenant's area+type (`x/y`), damped by a
   `x/(x+5)` factor so a boutique landlord with 3-for-3 doesn't outrank a
   large firm with 30-for-60 on percentage alone
   ([landlord.py:101-107](landlord.py#L101-L107); pinned by
   `test_specialization_count_damping`,
   [test_engine.py:110-113](test_engine.py#L110-L113): 0.375 vs 0.429).
3. **`match_strength`** — its own explicit weighted blend of size/budget/
   geo/semantic (`STRENGTH_WEIGHTS`, [landlord.py:34](landlord.py#L34):
   0.25/0.15/0.30/0.30 — `type` is dropped here since it's already a hard
   filter), averaged over the landlord's top-3 fitting spaces; `None` (not
   0, not hidden) when nothing fits at all — a landlord with zero matches
   doesn't get a fake low number, it gets an honest "not computed"
   ([landlord.py:109-126](landlord.py#L109-L126)).

`ordering` (`0.40*match_number_saturated + 0.25*specialization +
0.35*match_strength`, [landlord.py:33](landlord.py#L33),
[136-140](landlord.py#L136-L140)) exists only to **sort** the list — it is
computed, used to `.sort()`, and then **never put in the JSON response**
(`assert "score" not in r`, [test_engine.py:94](test_engine.py#L94)). If
asked "why hide it": because a single blended number invites the "how is
this computed" question that a landlord-level ranking can't answer as
cleanly as three separate countable facts can.

---

## 6. The embedding pipeline + the from-scratch TF-IDF fallback ([semantic.py](semantic.py))

Three backends, tried in order at **import time**, most-capable first; the
live one is exposed everywhere (API + UI footer) so nobody sees "semantic
understanding" when it's actually keyword overlap:

1. **`sentence-transformers` (local dev only)** — full PyTorch, downloads
   `all-MiniLM-L6-v2` on first use ([semantic.py:59-68](semantic.py#L59-L68)).
2. **ONNX + precomputed vectors — the deployed path.** Same MiniLM model,
   exported to quantized ONNX (~23 MB) by
   [tools/precompute_embeddings.py](tools/precompute_embeddings.py) via CI.
   All ~600+ listing descriptions are embedded **offline** into
   `embeddings.npz` (~0.6 MB); at request time the server only has to embed
   the tenant's one-sentence query — `onnxruntime` is a ~40 MB dependency
   with no PyTorch needed on Vercel's serverless runtime
   ([semantic.py:71-113](semantic.py#L71-L113)). Descriptions not found in
   the precomputed cache (freshly scraped since the last embeddings run)
   are embedded on the fly with the *same* model — never faked or skipped.
3. **From-scratch TF-IDF (the fallback, honestly labeled "NOT semantic")**
   — pure Python: term frequency × inverse document frequency, cosine
   similarity, no library ([semantic.py:118-150](semantic.py#L118-L150)).
   Only matches literal shared words after stripping stopwords.

**The one-breath explanation of the embedding math** (have this ready
verbatim, [semantic.py:30-34](semantic.py#L30-L34)): MiniLM turns a
sentence into 384 numbers whose *direction* encodes meaning, learned from
roughly a billion sentence pairs. Mean-pool the per-token vectors, L2-
normalize, and the dot product of two normalized vectors **is** the cosine
of the angle between them — 1.0 means same meaning, 0 means unrelated.
That's the whole reason "bright sunlit" can score high against "excellent
natural light" with zero letters in common — proven live and logged in
HANDOFF §6.

The ONNX encode function ([semantic.py:94-111](semantic.py#L94-L111))
literally mirrors what `sentence-transformers` does internally — tokenize,
run the transformer, mask-weighted mean-pool over tokens, L2-normalize —
which is worth pointing out if asked "do you actually understand what the
library call does or did you just call it": the from-scratch version *is*
the answer.

`explain()` ([semantic.py:184-201](semantic.py#L184-L201)) is what produces
the quoted phrase in a reason line: for embedding backends it splits the
description into sentences and finds the one closest in meaning to the
query; for TF-IDF it lists the literal shared keywords instead — and even
that distinction is labeled (`kind == "phrase"` vs `"keywords"`).

---

## 7. The rent-estimate model, end to end ([price_model.py](price_model.py))

**The problem:** 92% of rents are "Upon request." A curious tenant still
wants *some* sense of price. The rule set (RULE 1-4 in the docstring,
[price_model.py:8-19](price_model.py#L8-L19)) exists so an estimate never
becomes a fake fact:

- **RULE 1 — informational only.** Estimates never enter scoring, hard
  filters, or the live count. Enforced by
  `test_estimates_never_touch_ranking_or_count`
  ([test_engine.py:282-300](test_engine.py#L282-L300)) — literally swaps
  the model in and out and asserts identical scores/order/counts.
- **RULE 2 — envelope gating.** A space only gets an estimate if its
  features fall inside the *training data's* per-feature range (±10%
  tolerance) — [price_model.py:192-195](price_model.py#L192-L195). A model
  trained on GFP office lofts must not price the Empire State Building.
- **RULE 3 — range, not a point.** The band is the model's *real*
  leave-one-out residual distribution, 10th–90th percentile
  ([price_model.py:146](price_model.py#L146),
  [163](price_model.py#L163)) — as wide as the model is actually wrong, not
  a made-up ±10%.
- **RULE 4 — self-gating.** Refuses to ship if fewer than 25 training rows
  or LOO MAE exceeds 30% of the mean rent
  ([price_model.py:54-55](price_model.py#L54-L55),
  [141-142](price_model.py#L141-L142)) — pinned by
  `test_model_self_gates_on_tiny_data`
  ([test_engine.py:314-323](test_engine.py#L314-L323)).

**The model itself:** ridge regression in closed form,
`w = (XᵀX + λI)⁻¹Xᵀy` ([price_model.py:123-127](price_model.py#L123-L127)).
With ~32 training rows and up to 6 candidate features, anything more
expressive (a tree ensemble, a neural net) would memorize noise instead of
learning signal — ridge's λ shrinks coefficients toward zero, exactly the
right regularization at this sample size.

**Features** ([price_model.py:58](price_model.py#L58)): `log(size)`,
building age, floor count, and haversine distance to two anchor centroids
(Plaza District and Union Square — proxies for "prime Midtown" and "prime
Midtown South"). **Landlord identity is deliberately excluded** — including
it would let the model learn "this landlord's buildings are cheap" instead
of *why* a building is priced the way it is; that's leakage, not signal
([price_model.py:31-32](price_model.py#L31-L32)). Near-constant columns
(e.g. if every training row is Manhattan) are dropped automatically so
`XᵀX` doesn't become ill-conditioned for a feature carrying zero
information ([price_model.py:114-117](price_model.py#L114-L117)).

**λ selection:** leave-one-out cross-validation over a fixed grid
`[0.01 .. 30]` ([price_model.py:53](price_model.py#L53),
[129-139](price_model.py#L129-L139)). At n≈32, k-fold CV wastes too much
of the already-small training set per fold; LOO is cheap here (32 solves of
a ≤6×6 linear system) and gives a near-unbiased error estimate — no
hand-tuning of λ.

**Current honest state (say this out loud, don't dodge it):** the model
trains on 32 published rents, **all from GFP**, spanning roughly $39–58/SF.
The LOO MAE (~$3.2/SF) looks great partly *because* the training range is
narrow — a tight range is easier to predict accurately. Envelope gating is
what keeps this honest in production: it refuses to speak about buildings
outside that narrow envelope rather than silently extrapolating, so only
~10 estimates actually ship. Coverage should widen automatically as Durst's
occasional numeric rents and future landlords accrue more published data —
no code change needed, just more training rows next week.

---

## 8. The CI chain — the automation that must not be "simplified"

Two workflows, chained on purpose:

1. **[refresh_data.yml](.github/workflows/refresh_data.yml)** — Mondays
   09:00 UTC + manual dispatch. Snapshots each landlord's row count, runs
   every `scrape_*.py`, rebuilds `spaces_clean.csv`/`dataset_meta.json` via
   `clean_dataset.py`, retrains the price model, then a **sanity guard**:
   if any landlord's row count falls below 50% of the previous run, the
   job aborts with `sys.exit(...)` *before* committing
   ([refresh_data.yml:66-78](.github/workflows/refresh_data.yml#L66-L78)) —
   protects against a silent site redesign quietly emptying the dataset.
   If anything changed, it commits, then **explicitly** runs
   `gh workflow run embeddings.yml --ref main`
   ([refresh_data.yml:93-97](.github/workflows/refresh_data.yml#L93-L97)).
2. **[embeddings.yml](.github/workflows/embeddings.yml)** — re-embeds every
   description via `tools/precompute_embeddings.py`, commits
   `embeddings.npz` + `models/`. *That* commit is a normal (human-looking)
   push, so Vercel's git integration deploys it automatically.

**The one non-obvious fact worth stating unprompted:** a `GITHUB_TOKEN`-
authored commit does **not** trigger other `on: push` workflows — GitHub
suppresses that to prevent infinite workflow loops. If step 1 just pushed
and stopped, the embeddings would silently go stale forever with no error
anywhere. That's why the refresh workflow *explicitly* dispatches the
embeddings workflow via the `gh` CLI instead of relying on the push trigger
— removing that one line would quietly break the whole chain a week later
with no visible failure. (I hit exactly this kind of stale-data trap today:
the weekly refresh added Durst/Vornado listings to a submarket, which made
a test's hardcoded 3-landlord assertion wrong — not a code bug, just a test
that needed to assert the *invariant*, not a dataset snapshot. Good example
of why `test_engine.py` should pin design decisions, not incidental data
shape.)

---

## 9. Practice quiz (written by Claude Code, NOT the missing Phase-6 quiz)

Ten questions in the spirit of what an interviewer might actually ask,
worked from the code so you can rehearse against the real files.

**Q1. Why did you check the network tab before writing a scraper?**
A: To find the cheapest correct approach. For GFP, the availabilities table
is empty in raw HTML — a headless browser would work but costs ~60 slow
page loads. The network tab showed a JSON API returning everything in one
call ([scrape_gfp.py:10-15](scrape_gfp.py#L10-L15)).

**Q2. Two listings both list "Upon request" for rent. How does the engine
treat them relative to a listing with a real $65/SF rent and a $50 budget?**
A: Both "Upon request" listings score `budget = 0.5` (neutral). The $65/SF
listing scores `max(0, 1 - 2*0.30) = 0.4` — actually *lower* than the
unknowns, because it's a known rent 30% over budget
([matching.py:206-209](matching.py#L206-L209)).

**Q3. Why is `term` collected but never scored?**
A: It's short/long lease preference — useful for the eventual landlord
contact/lead flow, but scoring it would require assumptions about term
availability the scraped data doesn't reliably capture. Rather than fake a
signal, it's stored and excluded from scoring entirely, with a test
enforcing zero score delta ([matching.py:22-24](matching.py#L22-L24),
`test_term_is_stored_but_never_scored`).

**Q4. Why does `specialization` use `x/(x+5)` instead of just `x/y`?**
A: Straight percentage rewards tiny portfolios: a landlord with 3 total
listings, all 3 in the target area, hits 100%. The damping factor
discounts low absolute counts so a firm with 30-of-60 (50%, damped to
~43%) can outrank a 3-of-3 boutique (100%, damped to ~37.5%) — confidence
should scale with sample size ([landlord.py:105](landlord.py#L105),
verified numerically in `test_specialization_count_damping`).

**Q5. Why leave-one-out CV instead of 5-fold or a train/test split for the
price model?**
A: n≈32. A 5-fold split leaves ~6 rows per fold — too few to trust a fold's
error estimate, and a held-out test split throws away training data the
model can't afford to lose. LOO uses n-1 rows for every fit and produces
n error observations, the best tradeoff at this size, and it's cheap here
(32 solves of a tiny linear system, [price_model.py:129-139](price_model.py#L129-L139)).

**Q6. What stops the price model from confidently pricing a building
nothing like its training data?**
A: The envelope check — every feature of a candidate building must fall
inside the training set's observed range (±10% tolerance) or `estimate()`
returns `None` instead of a number
([price_model.py:192-195](price_model.py#L192-L195)). This is RULE 2, and
it's the difference between "the model is honest about its limits" and
"the model will happily extrapolate garbage."

**Q7. Why is landlord identity excluded as a price-model feature even
though it would probably improve the fit?**
A: Because it *would* improve the fit for the wrong reason — leakage. With
data currently 100% GFP, "landlord = GFP" would be a near-perfect predictor
by definition, but that's memorizing "which CSV did this row come from,"
not learning what building fundamentals drive price
([price_model.py:31-32](price_model.py#L31-L32)).

**Q8. What happens the moment `sentence-transformers` isn't installed (as
on Vercel)?**
A: The import at the top of [semantic.py](semantic.py) throws, is caught,
and the module falls through to the ONNX backend — same MiniLM model, no
PyTorch, precomputed vectors for existing descriptions plus on-the-fly ONNX
encoding for anything new ([semantic.py:59-113](semantic.py#L59-L113)).
Both backends are tried in a fixed order at import time; whichever
succeeds sets `BACKEND`, which the API/UI display honestly.

**Q9. A tenant selects three neighborhoods. How does `geo` scoring work
across all three?**
A: `nearest_area()` computes haversine distance to every selected area's
centroid and keeps the minimum — a listing is scored against whichever of
the tenant's picks it's actually closest to, so adding a far-away area
never hurts a building near one of the others
([matching.py:221-231](matching.py#L221-L231),
`test_multi_area_uses_nearest`).

**Q10. Why does the landlord layer never show a combined percentage?**
A: Because a single blended number is the least defensible part of a
ranking — "why is Landlord A at 82% and B at 79%" has no clean answer once
weights are baked in. Three separate, literally countable signals
(a count, a portfolio-share fraction, and an average of real per-space
scores) each answer their own question and can be checked by hand against
the CSV. `ordering` exists purely to `.sort()` the list and is asserted
absent from the JSON response ([test_engine.py:94](test_engine.py#L94)).

---

## 10. If you only remember five things

1. Unknown → 0.5, always. Never a fake number, never a penalty.
2. Landlord ranking = 3 countable signals, no blended %.
3. Ridge + LOO at n≈32 because anything fancier memorizes noise; envelope
   gating stops it from ever speaking outside its training range.
4. Embeddings are precomputed in CI + run via ONNX at request time — zero
   PyTorch on the server, same model as local dev.
5. `GITHUB_TOKEN` pushes don't trigger `on: push` workflows — that's why
   the refresh job explicitly dispatches the embeddings job.
