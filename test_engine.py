"""
test_engine.py — SpaceRank NYC: engine test suite
=================================================
Fast, dependency-free checks (plain asserts, runnable with pytest OR
`python test_engine.py`). Tests pin down the DESIGN DECISIONS, not just the
happy path — if someone changes "unknown rent is neutral", a test fails.
"""

import math

from landlord import rank_landlords
from matching import (TenantRequest, haversine_km, rank_spaces, score_budget,
                      score_geo, score_size, score_type)

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
    # Times Square -> SoHo is about 4 km as the crow flies
    d = haversine_km(40.7580, -73.9855, 40.7230, -74.0000)
    assert 3.5 < d < 4.5
    assert haversine_km(40.7, -74.0, 40.7, -74.0) == 0.0


def test_geo_neutral_when_ungeocode():
    """DESIGN DECISION: a building we couldn't geocode scores neutral,
    it does not crash the ranking with NaN."""
    assert score_geo(NAN, NAN, "soho")[0] == 0.5
    assert score_geo(40.72, -74.0, None)[0] == 0.5   # no area requested
    assert score_geo(40.72, -74.0, "atlantis")[0] == 0.5  # unknown area


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
    soho = rank_spaces(TenantRequest(property_type="Office", area="SoHo"), top_n=3)
    mid  = rank_spaces(TenantRequest(property_type="Office", area="Midtown"), top_n=3)
    assert {r["building"] for r in soho} != {r["building"] for r in mid}


def test_landlord_v2_shape_and_bounds():
    res = rank_landlords(TenantRequest(property_type="Office", area="Midtown",
                                       description="renovated lobby"))
    assert {r["landlord"] for r in res} == {"GFP Real Estate",
                                            "Rudin Management", "SL Green"}
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
