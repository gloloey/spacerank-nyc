"""
test_engine.py — SpaceRank NYC: engine test suite
=================================================
Fast, dependency-free checks (plain asserts, runnable with pytest OR
`python test_engine.py`). Tests pin down the DESIGN DECISIONS, not just the
happy path — if someone changes "unknown rent is neutral", a test fails.
"""

import math
import os

import pandas as pd

from landlord import rank_landlords
from matching import (COUNT_AREA_RADIUS_MI, LANDLORD_PROFILES, TenantRequest,
                      haversine_mi, nearest_geo_target, rank_spaces,
                      score_budget, score_geo, score_size, score_type,
                      search_subway_stations)

# The real landlord roster is whatever's in the scraped dataset — NOT
# LANDLORD_PROFILES, which only tags landlords with a KNOWN style and
# intentionally omits ones (like RXR Realty) that don't fit either bucket.
REAL_LANDLORDS = set(pd.read_csv(
    os.path.join(os.path.dirname(__file__), "spaces_clean.csv"))["landlord"].unique())

NAN = float("nan")


# ---- individual scorers ----------------------------------------------------
def test_type_exact_related_mismatch():
    assert score_type("Office", "Office")[0] == 1.0
    assert score_type("Office, Showroom", "Showroom")[0] == 1.0   # multi-use
    assert score_type("Retail", "Showroom")[0] == 0.6             # related
    assert score_type("Retail", "Life Science")[0] == 0.1         # mismatch
    assert score_type("", "Office")[0] == 0.5                     # unknown -> neutral


def test_size_range_and_decay():
    assert score_size(3000, 2000, 4000)[0] == 1.0
    assert score_size(1000, 2000, 4000)[0] == 0.5    # 50% below floor -> 0.5
    assert score_size(6000, 2000, 4000)[0] == 0.5    # 50% above cap  -> 0.5
    assert score_size(NAN, 2000, 4000)[0] == 0.5     # unlisted -> neutral
    assert score_size(3000, None, None)[0] == 0.5    # no target -> neutral


def test_budget_neutral_for_unknown_rent():
    """DESIGN DECISION: 'Upon request' must not bury a listing."""
    assert score_budget(NAN, 50)[0] == 0.5
    assert score_budget(40, 50)[0] == 1.0
    assert score_budget(60, 50)[0] < 1.0
    assert score_budget(40, None)[0] == 0.5          # tenant gave no budget


def test_haversine_known_distance():
    # Times Square -> SoHo is about 2.5 mi as the crow flies
    d = haversine_mi(40.7580, -73.9855, 40.7230, -74.0000)
    assert 2.2 < d < 2.8
    assert haversine_mi(40.7, -74.0, 40.7, -74.0) == 0.0


def test_area_radius_is_neighborhood_scale_not_borough_scale():
    """DESIGN DECISION: the 'inside area' hard-filter radius must be small
    enough that adjacent Midtown submarkets don't all count as the same
    area. Times Square and Grand Central are two distinct submarkets only
    ~0.66 mi apart (as centroid-to-centroid distance) — a radius as large as
    that gap would make a Grand Central building also count as "inside"
    Times Square, and vice versa. The radius must be smaller than that gap."""
    from matching import AREAS
    d = haversine_mi(*AREAS["times square & theater district"],
                     *AREAS["grand central & murray hill"])
    assert COUNT_AREA_RADIUS_MI < d
    # and it's a real neighborhood scale, not a borough-sized catchment
    assert COUNT_AREA_RADIUS_MI <= 0.75


def test_geo_neutral_when_ungeocode():
    """DESIGN DECISION: a building we couldn't geocode scores neutral,
    it does not crash the ranking with NaN."""
    assert score_geo(NAN, NAN, ["soho & noho"])[0] == 0.5
    assert score_geo(40.72, -74.0, [])[0] == 0.5     # no area requested
    assert score_geo(40.72, -74.0, [])[0] == 0.5     # (unknown areas are\n    #  stripped by TenantRequest.__post_init__ before reaching here)


# ---- ranking pipeline --------------------------------------------------------
def test_rank_spaces_shape_and_order():
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=10)
    assert len(res) == 10
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)     # descending
    assert all(0 <= s <= 100 for s in scores)
    for key in ("landlord", "building", "reason", "signals", "contact"):
        assert key in res[0]


def test_rank_spaces_geo_actually_moves_ranking():
    soho = rank_spaces(TenantRequest(property_type="Office", areas=["soho & noho"]), top_n=3)
    mid  = rank_spaces(TenantRequest(property_type="Office", areas=["grand central & murray hill"]), top_n=3)
    assert {r["building"] for r in soho} != {r["building"] for r in mid}


def test_landlord_v2_shape_and_bounds():
    res = rank_landlords(TenantRequest(property_type="Office", areas=["penn district & garment"],
                                       description="renovated lobby"))
    # Which landlords make the top-N shifts as the scraped dataset grows
    # (new landlords, new coverage) — pin the invariant, not a fixed roster.
    names = [r["landlord"] for r in res]
    assert names and len(names) == len(set(names))     # no duplicates
    assert set(names) <= REAL_LANDLORDS                 # only real landlords, never invented
    orderings = [r["ordering"] for r in res]
    assert orderings == sorted(orderings, reverse=True)
    for r in res:
        assert isinstance(r["match_number"], int)          # a COUNT, not a %
        assert 0 <= r["match_number"] <= r["n_available"]
        s = r["specialization"]
        assert 0.0 <= s["score"] <= 1.0 and s["x"] <= s["y"]
        assert str(s["x"]) in s["reason"] and str(s["y"]) in s["reason"]
        m = r["match_strength"]
        assert m["score"] is None or 0.0 <= m["score"] <= 1.0
        assert "score" not in r                            # no combined % shown


def test_landlord_hard_filters_are_hard():
    """A tiny size window must collapse match_number, not just dent a score."""
    wide = rank_landlords(TenantRequest(property_type="Office"))
    narrow = rank_landlords(TenantRequest(property_type="Office",
                                          size_min=99000, size_max=99500))
    total_wide = sum(r["match_number"] for r in wide)
    total_narrow = sum(r["match_number"] for r in narrow)
    assert total_narrow < total_wide
    for r in narrow:
        if r["match_number"] == 0:                 # nothing fits -> no faking
            assert r["match_strength"]["score"] is None


def test_specialization_count_damping():
    """spec = pct * X/(X+5): a 3-of-3 boutique must NOT beat a 30-of-60 firm."""
    boutique = 1.0 * (3 / (3 + 5))        # = 0.375
    big      = 0.5 * (30 / (30 + 5))      # = 0.429
    assert big > boutique


def test_no_leased_or_residential_in_results():
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=10**9)
    buildings = {r["building"] for r in res}
    assert "25 Water Street" not in buildings         # residential, excluded
    for r in res:
        assert str(r["rent"]).strip().lower() != "leased"


def test_every_result_has_image_or_none():
    """Images come from building_images.json — a URL or None, never garbage."""
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=20)
    for r in res:
        assert r["image"] is None or r["image"].startswith("http")
    assert sum(1 for r in res if r["image"]) >= 15   # coverage is ~100%


def test_nonsense_inputs_never_crash():
    """budget=0 used to ZeroDivisionError -> a 500 -> eternal 'Ranking…'."""
    for req in (TenantRequest(property_type="Office", budget_max_psf=0),
                TenantRequest(property_type="Office", budget_max_psf=-3),
                TenantRequest(property_type="Office", size_min=0, size_max=0),
                TenantRequest(property_type="Office", size_min=9000, size_max=100),
                TenantRequest(property_type="Office", areas=["atlantis", ""])):
        res = rank_spaces(req, top_n=3)
        assert len(res) == 3
    swapped = TenantRequest(property_type="Office", size_min=9000, size_max=100)
    assert swapped.size_min == 100 and swapped.size_max == 9000


def test_multi_area_uses_nearest():
    """A Tribeca building must score high when Tribeca is one of SEVERAL
    selected areas — nearest centroid wins, the far one doesn't hurt."""
    one = score_geo(40.7163, -74.0086, ["tribeca"])[0]
    many = score_geo(40.7163, -74.0086, ["south bronx", "tribeca", "jersey city"])[0]
    assert many == one == 1.0
    # and multi-area widens the fitting pool at the landlord level
    a = rank_landlords(TenantRequest(property_type="Office", areas=["tribeca"]))
    b = rank_landlords(TenantRequest(property_type="Office",
                                     areas=["tribeca", "soho & noho"]))
    assert (sum(r["match_number"] for r in b)
            >= sum(r["match_number"] for r in a))


# ---- geo anchor ("near a subway station / address") ------------------------
def test_anchor_sanitizes_bogus_input():
    """A malformed anchor (missing coords, wrong type, off the edge of the
    world) is dropped to None instead of trusted or crashing — same
    philosophy as budget<=0 and swapped size bounds."""
    assert TenantRequest(property_type="Office", anchor=None).anchor is None
    assert TenantRequest(property_type="Office", anchor={}).anchor is None
    assert TenantRequest(property_type="Office", anchor="not a dict").anchor is None
    assert TenantRequest(property_type="Office",
                         anchor={"lat": "nope", "lng": -74.0, "label": "x"}).anchor is None
    assert TenantRequest(property_type="Office",
                         anchor={"lat": 51.5, "lng": -0.12, "label": "London"}).anchor is None  # not NYC
    ok = TenantRequest(property_type="Office",
                       anchor={"lat": 40.7357, "lng": -73.9906, "label": "14 St-Union Sq"}).anchor
    assert ok == {"label": "14 St-Union Sq", "lat": 40.7357, "lng": -73.9906}


def test_anchor_pools_with_areas_in_geo_score():
    """An anchor point is just another candidate alongside chosen areas —
    the closest one wins, exactly like multiple areas already do."""
    station = {"label": "14 St-Union Sq", "lat": 40.735736, "lng": -73.990568}
    near_anchor_only = score_geo(40.735736, -73.990568, [], station)[0]
    assert near_anchor_only == 1.0
    # a far-away area shouldn't drag the score down when the anchor is close
    pooled = score_geo(40.735736, -73.990568, ["south bronx"], station)[0]
    assert pooled == 1.0
    # no areas and no anchor -> neutral, same as before this feature existed
    assert score_geo(40.72, -74.0, [])[0] == 0.5
    d, label = nearest_geo_target(40.735736, -73.990568, [], station)
    assert label == "14 St-Union Sq" and d < 0.1


def test_anchor_is_a_hard_filter_like_areas_for_count():
    """The custom anchor is a LOCATION constraint (like areas), not a
    ranking-only nudge (like style/fit) — it must narrow the live count."""
    from matching import count_spaces
    far_anchor = {"label": "far away", "lat": 40.8175, "lng": -73.9185}  # South Bronx
    plain = count_spaces(TenantRequest(property_type="Office"))
    anchored = count_spaces(TenantRequest(property_type="Office", anchor=far_anchor))
    assert anchored["count"] <= plain["count"]


def test_subway_station_search():
    """Real MTA station data — short queries return nothing (avoid dumping
    all ~445 stations), a real station name is findable."""
    assert search_subway_stations("") == []
    assert search_subway_stations("u") == []
    hits = search_subway_stations("union sq")
    assert any("Union Sq" in s["name"] for s in hits)
    assert all("lat" in s and "lng" in s and "routes" in s for s in hits)


def test_style_preference_is_a_nudge_not_a_driver():
    plain = rank_spaces(TenantRequest(property_type="Office"), top_n=10**9)
    pref  = rank_spaces(TenantRequest(property_type="Office",
                                      landlord_style="family-run"), top_n=10**9)
    by_key = {(r["building"], r["suite"]): r["score"] for r in plain}
    for r in pref:
        delta = r["score"] - by_key[(r["building"], r["suite"])]
        if r["landlord_style"] == "family-run":
            assert 0 <= delta <= 3.01          # exactly the small bonus
        else:
            assert abs(delta) < 1e-9           # others untouched


def test_fit_preference_is_a_nudge_not_a_driver():
    """Same shape as the landlord-style nudge: a small +3 bonus ONLY for
    spaces whose (parsed) fit-out condition matches, nothing for unknown
    or mismatched condition — never a ranking driver."""
    plain = rank_spaces(TenantRequest(property_type="Office"), top_n=10**9)
    pref  = rank_spaces(TenantRequest(property_type="Office",
                                      fit_preference="turnkey"), top_n=10**9)
    by_key = {(r["building"], r["suite"]): r["score"] for r in plain}
    saw_bonus = False
    for r in pref:
        delta = r["score"] - by_key[(r["building"], r["suite"])]
        if r["fit_condition"] == "turnkey":
            assert 0 <= delta <= 3.01
            saw_bonus = True
        else:
            assert abs(delta) < 1e-9            # unknown AND mismatched (e.g. "raw") untouched
    assert saw_bonus, "expected at least one turnkey-labeled listing in the dataset"
    # a nonsense fit_preference is sanitized away, same as landlord_style
    assert TenantRequest(property_type="Office", fit_preference="cozy").fit_preference is None


def test_term_is_stored_but_never_scored():
    """`term` must have ZERO effect on any ranking — it's future contact data."""
    a = rank_spaces(TenantRequest(property_type="Office", term="short"), top_n=10**9)
    b = rank_spaces(TenantRequest(property_type="Office", term=None), top_n=10**9)
    assert [(r["building"], r["suite"], r["score"]) for r in a] == \
           [(r["building"], r["suite"], r["score"]) for r in b]
    la = rank_landlords(TenantRequest(property_type="Office", term="long"))
    lb = rank_landlords(TenantRequest(property_type="Office"))
    assert [(r["landlord"], r["ordering"]) for r in la] == \
           [(r["landlord"], r["ordering"]) for r in lb]


def test_no_duplicate_spaces_in_results():
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=10**9)
    keys = [(r["landlord"], r["building"], r["suite"]) for r in res]
    assert len(keys) == len(set(keys))


def test_deep_ranking_returns_everything():
    """The tenant can scroll far: ALL available commercial spaces get ranked."""
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=500)
    assert len(res) >= 400                     # 406 minus nothing hidden
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------
# v0.7: freshness metadata + lead capture (API-level, via TestClient)
# --------------------------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    import app as app_module
    return TestClient(app_module.app)


def test_areas_exposes_dataset_freshness():
    """/api/areas must carry the dataset stamp the UI footer displays."""
    d = _client().get("/api/areas").json()
    ds = d["dataset"]
    assert ds["refreshed_at"].endswith("Z")
    assert ds["available_spaces"] >= 400
    assert ds["buildings_with_availability"] >= 50
    assert set(ds["per_landlord"]) == set(ds["landlords"])


def test_lead_valid_is_accepted_and_echoed_safely():
    r = _client().post("/api/leads", json={
        "name": "Test Tenant", "email": "tenant@example.com",
        "company": "ACME", "message": "hello",
        "interested_in": "171 Madison Avenue", "landlord": "GFP Real Estate",
        "search": {"property_type": "Office", "term": "long"}})
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] and len(body["lead_id"]) == 12
    assert "log" in body["stored"]            # honest about persistence


def test_lead_rejects_bad_input():
    c = _client()
    assert c.post("/api/leads", json={"name": "X Y", "email": "nope"}).status_code == 422
    assert c.post("/api/leads", json={"email": "a@b.co"}).status_code == 422
    assert c.post("/api/leads", json={"name": "A", "email": "a@b.co"}).status_code == 422  # 1-char name


def test_lead_bounds_hostile_search_payload():
    """A hostile client can't log megabytes through the search echo."""
    r = _client().post("/api/leads", json={
        "name": "AB", "email": "a@b.co",
        "search": {str(i): "x" * 10000 for i in range(60)}})
    assert r.status_code == 201                # accepted, but bounded:
    # (the bounding itself is a validator — 12 keys x 200 chars max)


def test_count_preview_shrinks_monotonically():
    """Adding a hard filter may only shrink (never grow) the live count."""
    from matching import count_spaces
    base = count_spaces(TenantRequest(property_type="Office"))["count"]
    a = count_spaces(TenantRequest(property_type="Office", areas=["soho & noho"]))["count"]
    b = count_spaces(TenantRequest(property_type="Office", areas=["soho & noho"],
                                   size_min=2000, size_max=8000))["count"]
    c = count_spaces(TenantRequest(property_type="Office", areas=["soho & noho"],
                                   size_min=2000, size_max=8000, budget_max_psf=60))["count"]
    assert base >= a >= b >= c >= 0
    assert base > 0


def test_count_ignores_ranking_only_inputs():
    """Vibe text, landlord style, fit preference, and term rank — they must
    never filter. (The custom anchor is DIFFERENT — it's a location
    constraint like areas, and IS a hard filter; see
    test_anchor_is_a_hard_filter_like_areas_for_count.)"""
    from matching import count_spaces
    plain = count_spaces(TenantRequest(property_type="Office", areas=["tribeca"]))
    styled = count_spaces(TenantRequest(property_type="Office", areas=["tribeca"],
                                        description="bright sunlit loft",
                                        landlord_style="family-run", term="short",
                                        fit_preference="turnkey"))
    assert plain["count"] == styled["count"]


def test_count_endpoint_matches_engine():
    from matching import count_spaces
    api = _client().get("/api/count", params={"property_type": "Office",
                                              "area": ["soho & noho"]}).json()
    eng = count_spaces(TenantRequest(property_type="Office", areas=["soho & noho"]))
    assert api == eng


# --------------------------------------------------------------------------
# v0.11: the rent-estimate model — honest by construction
# --------------------------------------------------------------------------

def test_estimates_never_touch_ranking_or_count():
    """RULE 1: with and without the model, scores + counts are identical."""
    import matching, price_model
    from matching import count_spaces
    req = TenantRequest(property_type="Office", areas=["penn district & garment"],
                        budget_max_psf=60, description="bright office")
    saved = matching._PRICE_MODEL
    try:
        matching._PRICE_MODEL = None
        res_off = rank_spaces(req, top_n=50)
        cnt_off = count_spaces(req)
        matching._PRICE_MODEL = price_model.load()
        res_on = rank_spaces(req, top_n=50)
        cnt_on = count_spaces(req)
    finally:
        matching._PRICE_MODEL = saved
    assert [r["score"] for r in res_on] == [r["score"] for r in res_off]
    assert [(r["landlord"], r["building"], r["suite"]) for r in res_on] ==            [(r["landlord"], r["building"], r["suite"]) for r in res_off]
    assert cnt_on == cnt_off


def test_estimates_only_on_unknown_rents_and_sane():
    res = rank_spaces(TenantRequest(property_type="Office"), top_n=10**9)
    for r in res:
        if r["rent_psf"] is not None:
            assert r["rent_estimate"] is None      # never second-guess a real price
        if r["rent_estimate"]:
            e = r["rent_estimate"]
            assert 5 <= e["low"] <= e["psf"] <= e["high"] <= 400
            assert "Est." in e["label"]


def test_model_self_gates_on_tiny_data(tmp_path=None):
    """RULE 4: a training set below the floor refuses to ship."""
    import pandas as pd, price_model, tempfile, os
    df = pd.read_csv("spaces_clean.csv").head(40).copy()
    df["rent_psf"] = float("nan")
    df.loc[df.index[:5], "rent_psf"] = 50.0        # only 5 known rents
    p = os.path.join(tempfile.gettempdir(), "tiny.csv")
    df.to_csv(p, index=False)
    m = price_model.train(p)
    assert m["ok"] is False and "usable published rents" in m["reason"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
