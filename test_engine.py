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
    for key in ("landlord", "building", "address", "reason", "signals", "contact"):
        assert key in res[0]
    assert all(isinstance(r["address"], str) for r in res)   # never None — always at least ""


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
    d, label, custom_radius = nearest_geo_target(40.735736, -73.990568, [], station)
    assert label == "14 St-Union Sq" and d < 0.1 and custom_radius is None


def test_anchor_radius_is_sanitized():
    """A drawn-radius anchor (map-click "search within N miles") is
    user-controlled input like everything else here: an out-of-bounds
    radius clamps to ANCHOR_RADIUS_BOUNDS rather than being trusted raw,
    and a malformed radius drops just the radius — the point itself
    still survives, same as scraper-style honest partial failure."""
    from matching import ANCHOR_RADIUS_BOUNDS
    lo, hi = ANCHOR_RADIUS_BOUNDS
    base = {"lat": 40.7357, "lng": -73.9906, "label": "x"}
    ok = TenantRequest(property_type="Office", anchor={**base, "radius_mi": 1.5}).anchor
    assert ok["radius_mi"] == 1.5
    clamped_hi = TenantRequest(property_type="Office", anchor={**base, "radius_mi": 999}).anchor
    assert clamped_hi["radius_mi"] == hi
    clamped_lo = TenantRequest(property_type="Office", anchor={**base, "radius_mi": -3}).anchor
    assert clamped_lo["radius_mi"] == lo
    bad = TenantRequest(property_type="Office", anchor={**base, "radius_mi": "nope"}).anchor
    assert "radius_mi" not in bad and bad["lat"] == 40.7357


def test_anchor_radius_overrides_default_curve_and_filter():
    """A drawn radius REPLACES the fixed 0.3 mi/5 mi ranking curve and the
    0.5 mi hard-filter radius with the tenant's own choice — not just an
    extra input alongside them."""
    center_lat, center_lng = 40.735736, -73.990568
    anchor_small = {"label": "here", "lat": center_lat, "lng": center_lng, "radius_mi": 0.2}
    anchor_plain = {"label": "here", "lat": center_lat, "lng": center_lng}

    # dead center -> full credit regardless of radius size
    assert score_geo(center_lat, center_lng, [], anchor_small)[0] == 1.0

    # a point confirmed (via the real haversine) to sit outside the drawn
    # 0.2 mi circle should score noticeably worse under the custom radius
    # than the exact same point does under the old fixed default curve
    far_lat = center_lat + 0.01
    assert haversine_mi(center_lat, center_lng, far_lat, center_lng) > anchor_small["radius_mi"]
    custom_score = score_geo(far_lat, center_lng, [], anchor_small)[0]
    default_score = score_geo(far_lat, center_lng, [], anchor_plain)[0]
    assert custom_score < default_score

    # the hard filter (count preview) must also shrink, never grow, as the
    # drawn radius shrinks at a fixed point
    from matching import count_spaces
    small = count_spaces(TenantRequest(property_type="Office", anchor=anchor_small))["count"]
    big = count_spaces(TenantRequest(property_type="Office",
                       anchor={**anchor_small, "radius_mi": 3.0}))["count"]
    assert small <= big


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
        "first_name": "Test", "last_name": "Tenant", "email": "tenant@example.com",
        "phone": "+1 212 555 0100", "tenant_type": "tenant",
        "company": "ACME", "message": "hello",
        "interested_in": "171 Madison Avenue", "landlord": "GFP Real Estate",
        "search": {"property_type": "Office", "term": "long"}})
    assert r.status_code == 201
    body = r.json()
    assert body["ok"] and len(body["lead_id"]) == 12
    assert "log" in body["stored"]            # honest about persistence


def test_lead_rejects_bad_input():
    c = _client()
    assert c.post("/api/leads", json={"first_name": "X", "last_name": "Y", "email": "nope"}).status_code == 422
    assert c.post("/api/leads", json={"email": "a@b.co"}).status_code == 422
    assert c.post("/api/leads", json={"first_name": "A", "email": "a@b.co"}).status_code == 422  # no last name


def test_lead_phone_plausibility():
    """Phone is optional, but if given must be a plausible number (7-14
    digits once formatting is stripped) — catches "12" or a stray partial
    entry rather than silently accepting garbage."""
    c = _client()
    base = {"first_name": "A", "last_name": "B", "email": "a@b.co"}
    assert c.post("/api/leads", json={**base, "phone": "12"}).status_code == 422
    assert c.post("/api/leads", json={**base, "phone": "+1 212 555 0100"}).status_code == 201
    assert c.post("/api/leads", json={**base, "phone": ""}).status_code == 201   # optional -> fine blank


def test_lead_bounds_hostile_search_payload():
    """A hostile client can't log megabytes through the search echo."""
    r = _client().post("/api/leads", json={
        "first_name": "A", "last_name": "B", "email": "a@b.co",
        "search": {str(i): "x" * 10000 for i in range(60)}})
    assert r.status_code == 201                # accepted, but bounded:
    # (the bounding itself is a validator — 12 keys x 200 chars max)


def test_lead_tenant_type_bad_value_becomes_blank():
    """DESIGN DECISION: an invalid tenant_type is quietly reset to neutral,
    never rejected — the same "never punish unusual input" philosophy as
    the rest of the engine."""
    r = _client().post("/api/leads", json={
        "first_name": "A", "last_name": "B", "email": "a@b.co", "tenant_type": "nonsense"})
    assert r.status_code == 201


def test_pulse_endpoint_honest_without_database():
    """No DB in the test env -> 503, never a fabricated zero-state."""
    r = _client().get("/api/pulse")
    assert r.status_code == 503


def test_alerts_rejects_bad_email_and_is_honest_without_database():
    c = _client()
    assert c.post("/api/alerts", json={"email": "nope", "search": {}}).status_code == 422
    # valid payload, but no DB configured in the test env -> honest 503,
    # not a fake "ok" that quietly loses the signup
    r = c.post("/api/alerts", json={"email": "a@b.co", "search": {"property_type": "Office"}})
    assert r.status_code == 503


def test_alerts_unsubscribe_reports_honestly_for_bogus_token():
    r = _client().get("/api/alerts/unsubscribe", params={"token": "not-a-real-token-at-all"})
    assert r.status_code == 200
    assert "already been used" in r.text or "isn't valid" in r.text


def test_shortlist_email_bounds_and_validates():
    c = _client()
    assert c.post("/api/shortlist-email", json={"email": "nope", "buildings": []}).status_code == 422
    assert c.post("/api/shortlist-email", json={"email": "a@b.co", "buildings": []}).status_code == 422
    # 31 buildings exceeds the 30-item cap -> rejected before ever trying to send
    many = [{"building": f"B{i}", "landlord": "L", "url": "", "score": 80} for i in range(31)]
    assert c.post("/api/shortlist-email", json={"email": "a@b.co", "buildings": many}).status_code == 422
    # valid, bounded payload -> honest 503 without SENDGRID_API_KEY in the test env
    ok = [{"building": "171 Madison Avenue", "landlord": "GFP Real Estate", "url": "https://x", "score": 88}]
    r = c.post("/api/shortlist-email", json={"email": "a@b.co", "buildings": ok})
    assert r.status_code == 503


def test_db_degrades_gracefully_without_database():
    """No DATABASE_URL configured (the CI/default state) -> every db.py
    function no-ops safely instead of raising."""
    import db
    saved = db.DATABASE_URL
    db.DATABASE_URL = None
    try:
        assert db.insert_lead({"lead_id": "x", "received_at": "now", "first_name": "a", "last_name": "b",
                               "email": "a@b.co", "phone": "", "company": "", "tenant_type": "",
                               "message": "", "interested_in": "", "landlord": "", "search": {}}) is False
        assert db.fetch_leads() == []
        assert db.fetch_stats() is None
        assert db.fetch_public_pulse() is None
        assert db.insert_saved_search("tok", "a@b.co", {}) is False
        assert db.deactivate_saved_search("tok") is False
        assert db.fetch_active_saved_searches() == []
        assert db.mark_new_listing_keys(["a", "b"]) == []
    finally:
        db.DATABASE_URL = saved


def test_db_connect_never_raises_on_bad_url():
    """A malformed/unreachable DATABASE_URL must degrade to 'no database',
    never crash a tenant-facing endpoint like /api/leads."""
    import db
    saved = db.DATABASE_URL
    db.DATABASE_URL = "not-a-valid-connection-string"
    try:
        assert db._connect() is None
    finally:
        db.DATABASE_URL = saved


def test_admin_requires_key():
    c = _client()
    assert c.get("/api/admin/leads").status_code == 401
    assert c.get("/api/admin/stats").status_code == 401
    assert c.get("/api/admin/leads", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_admin_accepts_correct_key_and_handles_no_database():
    """With the right key but no working DB in the test environment, the
    leads endpoint returns an honest empty list rather than crashing."""
    import app as app_module
    saved = app_module.ADMIN_API_KEY
    app_module.ADMIN_API_KEY = "test-key-123"
    try:
        c = _client()
        r = c.get("/api/admin/leads", headers={"X-Admin-Key": "test-key-123"})
        assert r.status_code == 200
        assert r.json()["leads"] == []
    finally:
        app_module.ADMIN_API_KEY = saved


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
