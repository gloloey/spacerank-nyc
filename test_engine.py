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


def test_landlord_aggregation():
    res = rank_landlords(TenantRequest(property_type="Office", area="Midtown"))
    assert len(res) == 3                              # GFP, Rudin, SL Green
    names = {r["landlord"] for r in res}
    assert names == {"GFP Real Estate", "Rudin Management", "SL Green"}
    for r in res:
        assert r["n_fitting"] <= r["n_available"]
        assert 0 <= r["score"] <= 100


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
