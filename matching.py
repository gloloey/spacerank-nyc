"""
matching.py — SpaceRank NYC: the matching engine (Layer 2, the spine) — v3
==========================================================================
Given a tenant request, score EVERY available space on five signals and blend
them into one 0-100 number, with a human-readable reason per result.

THE FIVE SIGNALS (each scored 0..1, then weighted):

  type      does the space's use (Office/Retail/...) match what they asked for?
  size      how close is the space to their target square footage?
  budget    is the asking rent within their $/SF/year budget?
  geo       distance (haversine) to the NEAREST of the tenant's chosen
            areas AND/OR a single custom ANCHOR point (a subway station
            or a geocoded address, e.g. "closer to my other office") —
            all candidate points are pooled and the closest one wins
  semantic  meaning-similarity between their free-text wish and the
            building's description (see semantic.py)

PLUS two deliberately small nudges (tiebreakers, never ranking drivers):
  landlord style — if the tenant prefers an institutional or family-run
  landlord, matching spaces get a flat +STYLE_BONUS points, noted in the
  reason line.
  fit-out condition — if the tenant prefers turnkey/move-in-ready or a
  raw/shell space, spaces whose LISTING TEXT says so (parsed at cleaning
  time — see clean_dataset.py:parse_fit_condition) get +FIT_BONUS points.
  Only ~16% of listings mention condition at all; the rest are neutral,
  never guessed.

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

import price_model as _pm
import semantic

WEIGHTS = {"type": 0.20, "size": 0.20, "budget": 0.15, "geo": 0.25, "semantic": 0.20}
STYLE_BONUS = 3.0        # points (of 100) when the landlord matches the
                         # tenant's style preference — small on purpose
FIT_BONUS = 3.0          # points (of 100) when the space's fit-out condition
                         # matches the tenant's preference — same size as
                         # STYLE_BONUS, same reasoning: a tiebreaker only
FIT_LABELS = {"turnkey": "Turnkey / move-in ready", "raw": "Raw / shell condition"}

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
    "Brookfield Properties":     "institutional",   # part of Brookfield Asset Mgmt (NYSE: BN)
    "Silverstein Properties":    "family-run",      # Silverstein family since 1957
    "Paramount Group":           "institutional",   # public REIT (NYSE: PGRE)
    "Jack Resnick & Sons":       "family-run",      # Resnick family, founded 1928
    "The Feil Organization":     "family-run",      # Feil family, still family-led
    "Two Trees Management":      "family-run",      # founded by David Walentas, CEO son Jed Walentas
    "George Comfort & Sons":     "family-run",      # Comfort family, still family-led
    "Brause Realty":             "family-run",      # Brause family (David, Roberta, Melissa Brause Rackoff)
    "Tishman Speyer":            "family-run",      # co-founded by Robert Tishman & Jerry Speyer; CEO Rob Speyer is Jerry's son
    # RXR Realty, Sage Realty, Time Equities, and Savanna are intentionally
    # NOT classified: privately held but none has a citable public source
    # pinning down either "public REIT" or a multi-generation family firm in
    # the Rudin/Durst/GFP/Silverstein/Tishman-Speyer sense — landlord_style
    # stays None rather than forcing them into either bucket.
}
STYLE_LABELS = {"institutional": "Institutional (public REIT)",
                "family-run": "Family-run firm"}

# ---------------------------------------------------------------------------
# AREAS: NYC office SUBMARKETS at consistent granularity (CBRE/JLL-style
# districts, ~0.3-0.6 mi radius each — real NYC neighborhood scale, not a
# borough-sized area). Keys are lowercase ids; AREA_LABELS holds display
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

# ---------------------------------------------------------------------------
# Subway stations: a real, committed snapshot of the MTA's own public station
# list (see tools/build_subway_stations.py — same cache pattern as
# pluto_cache.json / geocode_cache.json). Lets a tenant anchor their search
# on "near Union Sq" instead of only a whole submarket.
# ---------------------------------------------------------------------------
_HERE_ANCHOR = os.path.dirname(os.path.abspath(__file__))
try:
    import json as _json_stations
    with open(os.path.join(_HERE_ANCHOR, "subway_stations.json"), encoding="utf-8") as _f:
        SUBWAY_STATIONS = _json_stations.load(_f)
except Exception:
    SUBWAY_STATIONS = []
_STATIONS_BY_ID = {s["id"]: s for s in SUBWAY_STATIONS}

# permissive NYC-metro bounding box — anything outside this is discarded as
# a nonsense/mistyped anchor rather than trusted (same spirit as
# _clean_positive below: neutralize garbage input instead of crashing on it)
_NYC_BBOX = {"lat": (40.4, 41.1), "lng": (-74.35, -73.55)}


def search_subway_stations(query, limit=8):
    """Case-insensitive substring search over station names, name-prefix
    matches ranked first. Empty/short query returns nothing (avoid dumping
    all 445 stations into a dropdown before the tenant's typed anything)."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    starts, contains = [], []
    for s in SUBWAY_STATIONS:
        name = s["name"].lower()
        if name.startswith(q):
            starts.append(s)
        elif q in name:
            contains.append(s)
    return (starts + contains)[:limit]


def find_station(station_id):
    return _STATIONS_BY_ID.get(str(station_id))


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
    fit_preference: str | None = None      # "turnkey" | "raw" | None
    anchor: dict | None = None             # {"label","lat","lng"} — a single
                                            # custom "near here" point (a
                                            # subway station or a geocoded
                                            # address), pooled with `areas`
                                            # in the geo signal (see score_geo)
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
        if self.fit_preference not in FIT_LABELS:
            self.fit_preference = None
        self.anchor = _clean_anchor(self.anchor)


def _clean_anchor(anchor):
    """A custom geo-anchor is user-controlled input (a picked station or a
    geocoded address) — validate it the same way as every other field here:
    silently drop it to None on anything malformed rather than crash or
    trust a bogus point halfway across the world."""
    if not isinstance(anchor, dict):
        return None
    try:
        lat, lng = float(anchor.get("lat")), float(anchor.get("lng"))
    except (TypeError, ValueError):
        return None
    if math.isnan(lat) or math.isnan(lng):
        return None
    if not (_NYC_BBOX["lat"][0] <= lat <= _NYC_BBOX["lat"][1]
            and _NYC_BBOX["lng"][0] <= lng <= _NYC_BBOX["lng"][1]):
        return None
    label = str(anchor.get("label") or "your location").strip()[:80]
    return {"label": label, "lat": lat, "lng": lng}


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


def haversine_mi(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points on Earth, in miles."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_geo_target(lat, lng, area_keys, anchor=None):
    """(distance_mi, label) of the closest point among the tenant's chosen
    areas AND their custom anchor (a subway station or geocoded address),
    pooled together — a tenant near either "SoHo" or "my other office" is
    a fit. (None, None) if neither areas nor an anchor were given."""
    if lat != lat or lng != lng:                     # NaN -> unknown location
        return None, None
    best = None
    for k in area_keys:
        c = AREAS[k]
        d = haversine_mi(lat, lng, c[0], c[1])
        if best is None or d < best[0]:
            best = (d, AREA_LABELS[k])
    if anchor:
        d = haversine_mi(lat, lng, anchor["lat"], anchor["lng"])
        if best is None or d < best[0]:
            best = (d, anchor["label"])
    return best if best else (None, None)


def score_geo(lat, lng, area_keys, anchor=None):
    """Distance to the NEAREST selected area OR custom anchor point: 1.0
    within 0.3 mi, fading to 0 at 5 mi. Several candidates = several
    acceptable centers, the closest one counts."""
    if not area_keys and not anchor:
        return 0.5, "no area requested", None
    d, label = nearest_geo_target(lat, lng, area_keys, anchor)
    if d is None:
        return 0.5, "location unknown", None
    score = 1.0 if d <= 0.3 else max(0.0, 1 - (d - 0.3) / 4.7)
    return score, f"{d:.1f} mi from {label}", d


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


COUNT_AREA_RADIUS_MI = 0.5    # same hard-filter radius the landlord layer uses
                              # (see NYC_NEIGHBORHOOD_RADIUS note in landlord.py:
                              #  ~0.5 mi is real NYC neighborhood scale — the old
                              #  2 km/1.24 mi radius was large enough that most
                              #  adjacent Midtown submarkets bled into each other)


def count_spaces(req: TenantRequest, csv_path: str | None = None):
    """LIVE COUNT PREVIEW for the search button: how many available spaces
    pass the tenant's HARD filters right now, before they even search.

    Deliberately mirrors landlord.passes_hard_filters (one truth, two uses):
      type    strict (exact match only)
      size    within range, when a range is given
      budget  rejects only KNOWN rents above budget ("Upon request" passes)
      area    within 0.5 mi of ANY selected submarket OR the custom anchor
              point, if either was given; un-geocoded spaces fail
    The free-text description, landlord style, fit preference, and term
    are RANKING inputs, not filters — they never change this number
    (tests enforce it).
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
        if req.areas or req.anchor:
            lat, lng = _n(row.get("lat")), _n(row.get("lng"))
            if lat is None or lng is None:
                continue
            d, _label = nearest_geo_target(lat, lng, req.areas, req.anchor)
            if d is None or d > COUNT_AREA_RADIUS_MI:
                continue
        n += 1
        ll = _s(row["landlord"])
        per_landlord[ll] = per_landlord.get(ll, 0) + 1

    return {"count": n, "total": total, "per_landlord": per_landlord}


_PRICE_MODEL = _pm.load()      # None unless a trained, self-approved model exists


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
        s_geo, r_geo, _d = score_geo(row["lat"], row["lng"], req.areas, req.anchor)

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

        # small, transparent fit-out-condition nudge (a tiebreaker by design;
        # unknown/mismatched condition gets neither bonus NOR penalty)
        fit = _s(row.get("fit_condition")) or None
        fit_note = ""
        if req.fit_preference and fit == req.fit_preference:
            total = min(100.0, total + FIT_BONUS)
            fit_note = f"; {FIT_LABELS[fit].lower()} (your preference, +{FIT_BONUS:.0f})"

        # distance to the tenant's custom anchor point (a subway station or
        # geocoded address) — shown ALWAYS when an anchor is set, regardless
        # of whether it happened to be the nearest candidate for scoring
        anchor_mi = anchor_label = None
        if req.anchor:
            lat_v, lng_v = _n(row["lat"]), _n(row["lng"])
            if lat_v is not None and lng_v is not None:
                anchor_mi = round(haversine_mi(lat_v, lng_v, req.anchor["lat"], req.anchor["lng"]), 2)
            anchor_label = req.anchor["label"]

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
            "image": (lambda _i: None if not _i or _i.startswith("data:") else _i)(
                BUILDING_IMAGES.get(slug) or _s(row.get("image_url"))),
            "lat": _n(row["lat"]),
            "lng": _n(row["lng"]),
            "year_built": None if row.get("year_built") != row.get("year_built")
                          else int(row["year_built"]),
            "reason": f"{r_type}; {r_size}; {r_budg}; {r_geo}; "
                      f"description match {sem:.2f}{style_note}{fit_note}",
            "signals": {"type": s_type, "size": s_size, "budget": s_budg,
                        "geo": s_geo, "semantic": round(sem, 3)},
            "fit_condition": fit,
            "anchor_distance_mi": anchor_mi,
            "anchor_label": anchor_label,
            # RULE 1 (price_model.py): estimates are informational only.
            # They exist ONLY for display on unknown-rent spaces — scoring
            # and counting above never see them (test-enforced).
            "rent_estimate": (_pm.estimate(_PRICE_MODEL, {
                "space_type": row["space_type"], "size_sqft": row["size_sqft"],
                "lat": row["lat"], "lng": row["lng"],
                "year_built": row.get("year_built"), "floors": row.get("floors"),
            }) if _n(row.get("rent_psf")) is None else None),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]
