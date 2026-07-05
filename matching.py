"""
matching.py — SpaceRank NYC: the matching engine (Layer 2, the spine)
=====================================================================
Given a tenant request, score EVERY available space on five signals and blend
them into one 0-100 number, with a human-readable reason per result.

THE FIVE SIGNALS (each scored 0..1, then weighted):

  type      does the space's use (Office/Retail/...) match what they asked for?
  size      how close is the space to their target square footage?
  budget    is the asking rent within their $/SF/year budget?
  geo       real distance (haversine formula) from where they want to be
  semantic  meaning-similarity between their free-text wish and the
            building's description (see semantic.py)

DESIGN RULES (defensible in an interview):
  * Explicit weights, visible in one place (WEIGHTS below).
  * Unknown data gets a NEUTRAL score (0.5), never a fake good/bad one —
    e.g. "rent upon request" shouldn't sink a great space, nor boost it.
  * Every result carries its per-signal scores + a reason string, because a
    ranking you can't explain is a ranking you can't defend.

Usage:
    from matching import TenantRequest, rank_spaces
    results = rank_spaces(TenantRequest(...), top_n=5)
"""

import math
import os
from dataclasses import dataclass, field

import pandas as pd

import semantic

# ---------------------------------------------------------------------------
# The blend. Geo + semantic carry the most because they're what makes this
# product different from a plain filter form.
# ---------------------------------------------------------------------------
WEIGHTS = {"type": 0.20, "size": 0.20, "budget": 0.15, "geo": 0.25, "semantic": 0.20}

# Area name -> (lat, lng). Approximate centroids; good enough for ranking.
AREAS = {
    "soho":             (40.7230, -74.0000),
    "noho":             (40.7285, -73.9920),
    "tribeca":          (40.7163, -74.0086),
    "financial district": (40.7070, -74.0090),
    "times square":     (40.7580, -73.9855),
    "garment district": (40.7537, -73.9900),
    "midtown":          (40.7549, -73.9840),
    "plaza district":   (40.7625, -73.9722),
    "chelsea":          (40.7465, -74.0014),
    "flatiron":         (40.7411, -73.9897),
    "nomad":            (40.7448, -73.9880),
    "union square":     (40.7359, -73.9904),
    "greenwich village": (40.7336, -73.9996),
    "west village":     (40.7358, -74.0048),
    "hudson square":    (40.7263, -74.0056),
    "hell's kitchen":   (40.7638, -73.9918),
    "long island city": (40.7447, -73.9485),
    "williamsburg":     (40.7144, -73.9573),
    "bronx":            (40.8175, -73.9185),
    "jersey city":      (40.7178, -74.0431),
}


@dataclass
class TenantRequest:
    """What the tenant fills in on the site. Everything optional except type."""
    property_type: str                    # "Office", "Retail", "Showroom", ...
    size_min: float | None = None         # sq ft
    size_max: float | None = None
    budget_max_psf: float | None = None   # $ per sq ft per year
    area: str | None = None               # e.g. "SoHo" (looked up in AREAS)
    description: str = ""                 # free text — feeds the semantic layer
    weights: dict = field(default_factory=lambda: dict(WEIGHTS))


# ---------------------------------------------------------------------------
# Individual scoring functions — each returns (score 0..1, reason fragment)
# ---------------------------------------------------------------------------
def score_type(space_type: str, wanted: str):
    """Exact-use match = 1, related use = 0.6, mismatch = 0.1."""
    if not isinstance(space_type, str) or not space_type:
        return 0.5, "type unlisted"
    listed = [t.strip().lower() for t in space_type.split(",")]
    w = wanted.strip().lower()
    if w in listed:
        return 1.0, f"{wanted} space"
    related = {"retail": {"showroom"}, "showroom": {"retail", "office"},
               "office": {"showroom", "life science"}, "life science": {"office"}}
    if any(t in related.get(w, set()) for t in listed):
        return 0.6, f"{space_type} (related to {wanted})"
    return 0.1, f"{space_type}, not {wanted}"


def score_size(sqft, lo, hi):
    """1.0 inside the target range; decays with % deviation outside it.
    (e.g. 30% too small -> 0.7). No range given -> neutral."""
    if lo is None and hi is None:
        return 0.5, "no size target"
    if sqft is None or (isinstance(sqft, float) and math.isnan(sqft)):
        return 0.5, "size unlisted"
    lo = lo or 0
    hi = hi or float("inf")
    if lo <= sqft <= hi:
        return 1.0, f"{int(sqft):,} sf fits target"
    edge = lo if sqft < lo else hi
    deviation = abs(sqft - edge) / edge          # how far outside, relatively
    return max(0.0, 1 - deviation), f"{int(sqft):,} sf ({'below' if sqft < lo else 'above'} target)"


def score_budget(rent_psf, budget):
    """Within budget = 1; over budget decays by % overage; unknown = neutral."""
    if budget is None:
        return 0.5, "no budget given"
    if rent_psf is None or (isinstance(rent_psf, float) and math.isnan(rent_psf)):
        return 0.5, "rent on request"
    if rent_psf <= budget:
        return 1.0, f"${rent_psf:.0f}/SF within budget"
    overage = (rent_psf - budget) / budget
    return max(0.0, 1 - 2 * overage), f"${rent_psf:.0f}/SF over budget"


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points on Earth, in km.
    Turns coordinates into real 'how far is it actually' distance."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def score_geo(lat, lng, area):
    """<=0.5 km from the requested area = 1.0, fading linearly to 0 at 8 km —
    roughly 'anywhere in the same part of the city still counts a bit'."""
    if not area:
        return 0.5, "no area requested", None
    target = AREAS.get(area.strip().lower())
    if target is None:
        return 0.5, f"unknown area '{area}'", None
    if lat != lat or lng != lng:                 # NaN: building not geocoded
        return 0.5, "location unknown", None
    d = haversine_km(lat, lng, target[0], target[1])
    score = 1.0 if d <= 0.5 else max(0.0, 1 - (d - 0.5) / 7.5)
    return score, f"{d:.1f} km from {area}", d


# ---------------------------------------------------------------------------
# The ranking pipeline
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))   # works locally AND deployed


def rank_spaces(req: TenantRequest, top_n: int = 5, csv_path: str | None = None):
    csv_path = csv_path or os.path.join(_HERE, "spaces_clean.csv")
    df = pd.read_csv(csv_path)
    df = df[df["is_available"] & (df["building_use"] == "commercial")].copy()

    # Semantic scores for all rows at once (one pass is much faster).
    if req.description.strip():
        sem_scores = semantic.similarity(req.description, df["description"].fillna("").tolist())
    else:
        sem_scores = [0.5] * len(df)

    results = []
    for (_, row), sem in zip(df.iterrows(), sem_scores):
        s_type, r_type = score_type(row["space_type"], req.property_type)
        s_size, r_size = score_size(row["size_sqft"], req.size_min, req.size_max)
        s_budg, r_budg = score_budget(row["rent_psf"], req.budget_max_psf)
        s_geo, r_geo, _ = score_geo(row["lat"], row["lng"], req.area)

        w = req.weights
        total = (w["type"] * s_type + w["size"] * s_size + w["budget"] * s_budg
                 + w["geo"] * s_geo + w["semantic"] * sem)

        results.append({
            "score": round(100 * total, 1),
            "landlord": row["landlord"],
            "building": row["building_name"],
            "suite": row["floor_suite"],
            # NaN is not valid JSON — the API layer needs None instead
            "size_sqft": None if row["size_sqft"] != row["size_sqft"] else row["size_sqft"],
            "rent": row["rent"],
            # raw numeric rent (NaN -> None): lets the landlord layer apply a
            # HARD budget filter on known rents instead of guessing from the
            # blended budget score (which is 0.5 for both "unknown" and
            # "25% over budget" — ambiguous).
            "rent_psf": None if row["rent_psf"] != row["rent_psf"] else row["rent_psf"],
            "description": row["description"] if isinstance(row["description"], str) else "",
            "borough": row["borough"],
            "contact": (f"{row['contact_name']} <{row['contact_email']}>"
                        if isinstance(row["contact_email"], str) and row["contact_email"]
                        else str(row["contact_name"] or "")),
            "url": row["source_url"],
            "lat": None if row["lat"] != row["lat"] else row["lat"],
            "lng": None if row["lng"] != row["lng"] else row["lng"],
            "year_built": None if row.get("year_built", float("nan")) != row.get("year_built", float("nan")) else int(row["year_built"]),
            "reason": f"{r_type}; {r_size}; {r_budg}; {r_geo}; description match {sem:.2f}",
            "signals": {"type": s_type, "size": s_size, "budget": s_budg,
                        "geo": s_geo, "semantic": round(sem, 3)},
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]
