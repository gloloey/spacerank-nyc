"""
matching.py — SpaceRank NYC: the matching engine (Layer 2, the spine) — v3
==========================================================================
Given a tenant request, score EVERY available space on five signals and blend
them into one 0-100 number, with a human-readable reason per result.

THE FIVE SIGNALS (each scored 0..1, then weighted):

  type      does the space's use (Office/Retail/...) match what they asked for?
  size      how close is the space to their target square footage?
  budget    is the asking rent within their $/SF/year budget?
  geo       distance (haversine) to the NEAREST of the tenant's chosen areas —
            the tenant may now select SEVERAL submarkets
  semantic  meaning-similarity between their free-text wish and the
            building's description (see semantic.py)

PLUS one deliberately small nudge:
  landlord style — if the tenant prefers an institutional or family-run
  landlord, matching spaces get a flat +STYLE_BONUS points (3 on a 0-100
  scale — a tiebreaker, never a ranking driver), noted in the reason line.

AND one stored-only field:
  term ("short"/"long") — captured for the future landlord-contact flow,
  intentionally NEVER used in scoring (test_engine.py enforces this).

DESIGN RULES (defensible in an interview):
  * Explicit weights, visible in one place (WEIGHTS below).
  * Unknown data gets a NEUTRAL score (0.5), never a fake good/bad one.
  * Nonsense inputs are neutralized, not crashed on: budget <= 0 and
    size limits <= 0 are treated as "not provided".
  * Every result carries its per-signal scores + a reason string, and every
    value emitted to JSON is NaN-guarded.
"""

import math
import os
from dataclasses import dataclass, field

import pandas as pd

import semantic

WEIGHTS = {"type": 0.20, "size": 0.20, "budget": 0.15, "geo": 0.25, "semantic": 0.20}
STYLE_BONUS = 3.0        # points (of 100) when the landlord matches the
                         # tenant's style preference — small on purpose

# ---------------------------------------------------------------------------
# Landlord profiles — honest, verifiable classifications:
#   SL Green:  publicly traded REIT (NYSE: SLG), NYC's largest office landlord
#   Rudin:     family-owned and family-run since 1925
#   GFP:       family-owned (Gural family), value/loft-focused portfolio
# Only styles that actually exist in the data are offered as filters.
# ---------------------------------------------------------------------------
LANDLORD_PROFILES = {
    "SL Green":                  "institutional",   # public REIT (NYSE: SLG)
    "Rudin Management":          "family-run",      # Rudin family since 1925
    "GFP Real Estate":           "family-run",      # Gural family
    "Vornado Realty Trust":      "institutional",   # public REIT (NYSE: VNO)
    "The Durst Organization":    "family-run",      # Durst family since 1915
    "Empire State Realty Trust": "institutional",   # public REIT (NYSE: ESRT)
}
STYLE_LABELS = {"institutional": "Institutional (public REIT)",
                "family-run": "Family-run firm"}

# ---------------------------------------------------------------------------
# AREAS: NYC office SUBMARKETS at consistent granularity (CBRE/JLL-style
# districts, ~1-2 km each). Keys are lowercase ids; AREA_LABELS holds display
# names; AREA_GROUPS drives the grouped picker in the UI.
# ---------------------------------------------------------------------------
AREAS = {
    "financial district":               (40.7075, -74.0113),
    "tribeca":                          (40.7163, -74.0086),
    "soho & noho":                      (40.7248, -73.9973),
    "greenwich village & west village": (40.7340, -74.0014),
    "chelsea & meatpacking":            (40.7440, -74.0000),
    "flatiron & union square":          (40.7379, -73.9903),
    "gramercy & nomad":                 (40.7420, -73.9845),
    "penn district & garment":          (40.7519, -73.9911),
    "times square & theater district":  (40.7580, -73.9855),
    "grand central & murray hill":      (40.7513, -73.9765),
    "plaza district":                   (40.7625, -73.9722),
    "columbus circle & midtown west":   (40.7680, -73.9838),
    "long island city":                 (40.7447, -73.9485),
    "north brooklyn (williamsburg)":    (40.7144, -73.9573),
    "brooklyn navy yard":               (40.7005, -73.9720),
    "south bronx":                      (40.8175, -73.9185),
    "jersey city":                      (40.7178, -74.0431),
}
AREA_LABELS = {
    "financial district": "Financial District", "tribeca": "Tribeca",
    "soho & noho": "SoHo & NoHo",
    "greenwich village & west village": "Greenwich Village & West Village",
    "chelsea & meatpacking": "Chelsea & Meatpacking",
    "flatiron & union square": "Flatiron & Union Square",
    "gramercy & nomad": "Gramercy & NoMad",
    "penn district & garment": "Penn District & Garment",
    "times square & theater district": "Times Square & Theater District",
    "grand central & murray hill": "Grand Central & Murray Hill",
    "plaza district": "Plaza District",
    "columbus circle & midtown west": "Columbus Circle & Midtown West",
    "long island city": "Long Island City",
    "north brooklyn (williamsburg)": "North Brooklyn (Williamsburg)",
    "brooklyn navy yard": "Brooklyn Navy Yard",
    "south bronx": "South Bronx", "jersey city": "Jersey City",
}
AREA_GROUPS = {
    "Manhattan": ["financial district", "tribeca", "soho & noho",
                  "greenwich village & west village", "chelsea & meatpacking",
                  "flatiron & union square", "gramercy & nomad",
                  "penn district & garment", "times square & theater district",
                  "grand central & murray hill", "plaza district",
                  "columbus circle & midtown west"],
    "Beyond Manhattan": ["long island city", "north brooklyn (williamsburg)",
                         "brooklyn navy yard", "south bronx", "jersey city"],
}


def _clean_positive(x):
    """Nonsense numeric input (None, NaN, <= 0) -> None ('not provided')."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or x <= 0:
        return None
    return x


def valid_areas(areas):
    """Normalize a list of requested areas to known AREAS keys."""
    out = []
    for a in areas or []:
        k = str(a).strip().lower()
        if k in AREAS and k not in out:
            out.append(k)
    return out


@dataclass
class TenantRequest:
    """What the tenant fills in on the site. Everything optional except type."""
    property_type: str                     # "Office", "Retail", "Showroom", ...
    size_min: float | None = None          # sq ft
    size_max: float | None = None
    budget_max_psf: float | None = None    # $ per sq ft per year
    areas: list = field(default_factory=list)   # 0..n submarket keys
    description: str = ""                  # free text — feeds the semantic layer
    landlord_style: str | None = None      # "institutional" | "family-run" | None
    term: str | None = None                # "short" | "long" — STORED ONLY
    weights: dict = field(default_factory=lambda: dict(WEIGHTS))

    def __post_init__(self):
        # neutralize nonsense inputs instead of crashing deep in a scorer
        self.size_min = _clean_positive(self.size_min)
        self.size_max = _clean_positive(self.size_max)
        if (self.size_min is not None and self.size_max is not None
                and self.size_max < self.size_min):     # swapped bounds
            self.size_min, self.size_max = self.size_max, self.size_min
        self.budget_max_psf = _clean_positive(self.budget_max_psf)
        self.areas = valid_areas(self.areas)
        if self.landlord_style not in STYLE_LABELS:
            self.landlord_style = None


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
    """1.0 inside the target range; decays with % deviation outside it."""
    if lo is None and hi is None:
        return 0.5, "no size target"
    if sqft is None or (isinstance(sqft, float) and math.isnan(sqft)):
        return 0.5, "size unlisted"
    lo = lo or 0
    hi = hi if hi is not None else float("inf")
    if lo <= sqft <= hi:
        return 1.0, f"{int(sqft):,} sf fits target"
    edge = lo if sqft < lo else hi
    deviation = abs(sqft - edge) / max(edge, 1.0)
    return max(0.0, 1 - deviation), f"{int(sqft):,} sf ({'below' if sqft < lo else 'above'} target)"


def score_budget(rent_psf, budget):
    """Within budget = 1; over budget decays by % overage; unknown = neutral.
    budget is pre-sanitized by TenantRequest (never 0 here), but stay safe."""
    if budget is None or budget <= 0:
        return 0.5, "no budget given"
    if rent_psf is None or (isinstance(rent_psf, float) and math.isnan(rent_psf)):
        return 0.5, "rent on request"
    if rent_psf <= budget:
        return 1.0, f"${rent_psf:.0f}/SF within budget"
    overage = (rent_psf - budget) / budget
    return max(0.0, 1 - 2 * overage), f"${rent_psf:.0f}/SF over budget"


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points on Earth, in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_area(lat, lng, area_keys):
    """(distance_km, key) of the closest requested area, or (None, None)."""
    if lat != lat or lng != lng:                     # NaN -> unknown location
        return None, None
    best = None
    for k in area_keys:
        c = AREAS[k]
        d = haversine_km(lat, lng, c[0], c[1])
        if best is None or d < best[0]:
            best = (d, k)
    return best if best else (None, None)


def score_geo(lat, lng, area_keys):
    """Distance to the NEAREST selected area: 1.0 within 0.5 km, fading to 0
    at 8 km. Several areas = several acceptable centers, best one counts."""
    if not area_keys:
        return 0.5, "no area requested", None
    d, k = nearest_area(lat, lng, area_keys)
    if d is None:
        return 0.5, "location unknown", None
    score = 1.0 if d <= 0.5 else max(0.0, 1 - (d - 0.5) / 7.5)
    return score, f"{d:.1f} km from {AREA_LABELS[k]}", d


# ---------------------------------------------------------------------------
# The ranking pipeline
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))   # works locally AND deployed

# building photo per slug, collected from each landlord's own site
try:
    import json as _json
    with open(os.path.join(_HERE, "building_images.json"), encoding="utf-8") as _f:
        BUILDING_IMAGES = _json.load(_f)
except Exception:
    BUILDING_IMAGES = {}


def _s(v, default=""):
    """NaN-safe string: every text field emitted to JSON goes through here."""
    return v if isinstance(v, str) else default


def _n(v):
    """NaN-safe number: NaN -> None (valid JSON)."""
    return None if v is None or v != v else v


COUNT_AREA_RADIUS_KM = 2.0    # same hard-filter radius the landlord layer uses


def count_spaces(req: TenantRequest, csv_path: str | None = None):
    """LIVE COUNT PREVIEW for the search button: how many available spaces
    pass the tenant's HARD filters right now, before they even search.

    Deliberately mirrors landlord.passes_hard_filters (one truth, two uses):
      type    strict (exact match only)
      size    within range, when a range is given
      budget  rejects only KNOWN rents above budget ("Upon request" passes)
      area    within 2 km of ANY selected submarket; un-geocoded spaces fail
    The free-text description, landlord style, and term are RANKING inputs,
    not filters — they never change this number (tests enforce it).
    Cheap by construction: no semantic model is touched.
    """
    csv_path = csv_path or os.path.join(_HERE, "spaces_clean.csv")
    df = pd.read_csv(csv_path)
    df = df[df["is_available"] & (df["building_use"] == "commercial")]
    df = df.drop_duplicates(subset=["landlord", "building_name", "floor_suite"])

    total = len(df)
    n = 0
    per_landlord = {}
    for _, row in df.iterrows():
        s_type, _ = score_type(row["space_type"], req.property_type)
        if s_type != 1.0:
            continue
        if req.size_min is not None or req.size_max is not None:
            s_size, _ = score_size(row["size_sqft"], req.size_min, req.size_max)
            if s_size != 1.0:
                continue
        if req.budget_max_psf is not None:
            rent = _n(row.get("rent_psf"))
            if rent is not None and rent > req.budget_max_psf:
                continue
        if req.areas:
            lat, lng = _n(row.get("lat")), _n(row.get("lng"))
            if lat is None or lng is None:
                continue
            d, _k = nearest_area(lat, lng, req.areas)
            if d is None or d > COUNT_AREA_RADIUS_KM:
                continue
        n += 1
        ll = _s(row["landlord"])
        per_landlord[ll] = per_landlord.get(ll, 0) + 1

    return {"count": n, "total": total, "per_landlord": per_landlord}


def rank_spaces(req: TenantRequest, top_n: int = 5, csv_path: str | None = None):
    csv_path = csv_path or os.path.join(_HERE, "spaces_clean.csv")
    df = pd.read_csv(csv_path)
    df = df[df["is_available"] & (df["building_use"] == "commercial")].copy()
    # belt & suspenders: identical (landlord, building, suite) rows collapse
    df = df.drop_duplicates(subset=["landlord", "building_name", "floor_suite"])

    if req.description.strip():
        sem_scores = semantic.similarity(req.description, df["description"].fillna("").tolist())
    else:
        sem_scores = [0.5] * len(df)

    results = []
    for (_, row), sem in zip(df.iterrows(), sem_scores):
        s_type, r_type = score_type(row["space_type"], req.property_type)
        s_size, r_size = score_size(row["size_sqft"], req.size_min, req.size_max)
        s_budg, r_budg = score_budget(row["rent_psf"], req.budget_max_psf)
        s_geo, r_geo, _d = score_geo(row["lat"], row["lng"], req.areas)

        w = req.weights
        total = 100 * (w["type"] * s_type + w["size"] * s_size
                       + w["budget"] * s_budg + w["geo"] * s_geo
                       + w["semantic"] * sem)

        # small, transparent landlord-style nudge (a tiebreaker by design)
        style = LANDLORD_PROFILES.get(row["landlord"])
        style_note = ""
        if req.landlord_style and style == req.landlord_style:
            total = min(100.0, total + STYLE_BONUS)
            style_note = f"; {STYLE_LABELS[style].lower()} (your preference, +{STYLE_BONUS:.0f})"

        slug = _s(row["source_url"]).rstrip("/").split("/")[-1]
        results.append({
            "score": round(total, 1),
            "landlord": _s(row["landlord"]),
            "landlord_style": style,
            "building": _s(row["building_name"]),
            "suite": _s(row["floor_suite"]),
            "size_sqft": _n(row["size_sqft"]),
            "rent": _s(row["rent"]),
            "rent_psf": _n(row["rent_psf"]),
            "description": _s(row["description"]),
            "borough": _s(row["borough"]),
            "contact": (f"{_s(row['contact_name'])} <{row['contact_email']}>"
                        if _s(row["contact_email"]) else _s(row["contact_name"])),
            "url": _s(row["source_url"]),
            "image": BUILDING_IMAGES.get(slug),
            "lat": _n(row["lat"]),
            "lng": _n(row["lng"]),
            "year_built": None if row.get("year_built") != row.get("year_built")
                          else int(row["year_built"]),
            "reason": f"{r_type}; {r_size}; {r_budg}; {r_geo}; "
                      f"description match {sem:.2f}{style_note}",
            "signals": {"type": s_type, "size": s_size, "budget": s_budg,
                        "geo": s_geo, "semantic": round(sem, 3)},
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]
