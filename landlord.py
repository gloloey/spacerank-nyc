"""
landlord.py — SpaceRank NYC: Layer 3, the landlord ranking (v2)
===============================================================
Ranks landlords on exactly THREE transparent signals. Every number traces
back to real data; nothing is invented, nothing is double-counted, and the
tenant sees each signal separately — there is deliberately NO combined
percentage on display.

THE THREE SIGNALS
-----------------
1. match_number  (a plain COUNT — tenant label: "Spaces that fit you")
   How many of the landlord's currently-available spaces pass the tenant's
   HARD filters. Filter semantics (deliberate, documented):
     type    space's listed use must include the requested type   [strict]
     size    if a range was given: size must be known AND inside  [strict]
     budget  rejects only spaces with a KNOWN rent above budget — 92% of
             the market publishes "Upon request", and a missing price is
             not the same as a bad price                    [known-violation]
     area    if an area was given: building must be geocoded AND within
             AREA_RADIUS_KM of it. Unknown location FAILS — we will not
             claim a building is "in SoHo" without knowing where it is
                                                                   [strict]

2. specialization  (0-1 — tenant label: "Area & type expertise")
   How concentrated the landlord's available portfolio is in the tenant's
   area + type:  spec = (X/Y) x (X/(X+DAMP))
   where X = their available spaces of that area+type, Y = all their
   available spaces. The first factor is the percentage; the second damps
   it when the absolute count is small, so a 3-of-3 boutique (1.00 x 0.38
   = 0.38) does not beat a 101-of-208 giant (0.49 x 0.95 = 0.46).
   Percentage alone is never used.

3. match_strength  (0-1 — tenant label: "Spaces match quality")
   How well their best FITTING spaces (top MATCH_TOP of them) suit this
   request beyond the hard filters:
     strength = 0.25*size + 0.15*budget + 0.30*geo + 0.30*semantic
   The semantic part compares the tenant's free text with the space
   descriptions (embedding cosine similarity when sentence-transformers is
   installed; TF-IDF keyword overlap otherwise — semantic.BACKEND says
   which). The reason line quotes what actually matched, via
   semantic.explain(). If ZERO spaces pass the filters, strength is None —
   reported as not computable rather than faked.

ORDERING
--------
The list order blends the three (weights below). The blended value is
returned as "ordering" for sortability/debugging but is NOT meant to be
displayed — the product shows three transparent signals, not one opaque
score. match_number enters the blend as n/(n+SATURATION): smooth
diminishing returns instead of an arbitrary cap.
"""

from collections import defaultdict

import semantic
from matching import AREAS, TenantRequest, haversine_km, rank_spaces

ORDER_WEIGHTS = {"match_number": 0.40, "specialization": 0.25, "match_strength": 0.35}
STRENGTH_WEIGHTS = {"size": 0.25, "budget": 0.15, "geo": 0.30, "semantic": 0.30}
AREA_RADIUS_KM = 2.0     # "in the area" = within 2 km of the neighborhood centroid
DAMP = 5                 # pseudo-count damping for specialization
SATURATION = 10          # match_number -> ordering: n / (n + SATURATION)
MATCH_TOP = 3            # strength = mean over the landlord's top-3 fitting spaces


def _in_area(space, req):
    """True if the building is verifiably within AREA_RADIUS_KM of the
    requested area. Unknown coordinates -> False (never claim a location)."""
    target = AREAS.get((req.area or "").strip().lower())
    if target is None:
        return True                              # no (known) area requested
    lat, lng = space.get("lat"), space.get("lng")
    if lat is None or lng is None:
        return False
    return haversine_km(lat, lng, target[0], target[1]) <= AREA_RADIUS_KM


def passes_hard_filters(space, req):
    """The four hard filters behind match_number. See module docstring for
    the per-filter semantics (strict vs known-violation)."""
    if space["signals"]["type"] != 1.0:                          # type: strict
        return False
    if (req.size_min is not None or req.size_max is not None):   # size: strict
        if space["signals"]["size"] != 1.0:
            return False
    if req.budget_max_psf is not None:              # budget: known violations
        rent = space.get("rent_psf")
        if rent is not None and rent > req.budget_max_psf:
            return False
    return _in_area(space, req)                                  # area: strict


def _strength_of(space):
    """Soft fit of ONE space, 0..1, over the four graded dimensions."""
    sig = space["signals"]
    w = STRENGTH_WEIGHTS
    return (w["size"] * sig["size"] + w["budget"] * sig["budget"]
            + w["geo"] * sig["geo"] + w["semantic"] * sig["semantic"])


def rank_landlords(req: TenantRequest, top_n: int = 5,
                   csv_path: str | None = None):
    # Score every available space once; the landlord layer is aggregation
    # on top of the space layer — one engine, two views of its output.
    spaces = rank_spaces(req, top_n=10**9, csv_path=csv_path)

    by_landlord = defaultdict(list)
    for s in spaces:
        by_landlord[s["landlord"]].append(s)

    area_label = (req.area.strip().title() + " ") if req.area else ""
    results = []
    for name, sp in by_landlord.items():
        fitting = [s for s in sp if passes_hard_filters(s, req)]

        # ---- signal 1: match_number (a real count) -------------------------
        match_number = len(fitting)

        # ---- signal 2: specialization (count-damped share) -----------------
        y = len(sp)
        x = sum(1 for s in sp
                if s["signals"]["type"] == 1.0 and _in_area(s, req))
        specialization = (x / y) * (x / (x + DAMP)) if y else 0.0
        spec_reason = (f"{x} of their {y} available spaces are "
                       f"{area_label}{req.property_type}")

        # ---- signal 3: match_strength over their best fitting spaces -------
        if fitting:
            fitting.sort(key=_strength_of, reverse=True)
            top = fitting[:MATCH_TOP]
            match_strength = round(sum(_strength_of(s) for s in top) / len(top), 3)
            best = top[0]
            phrase, kind = semantic.explain(req.description, best["description"])
            if phrase:
                verb = "matches" if kind == "phrase" else "shares your keywords"
                strength_reason = f'{best["building"]} {verb} “{phrase}”'
            else:
                strength_reason = (f'best fit: {best["building"]} — '
                                   f'{best["suite"]}' if not req.description.strip()
                                   else f'no description overlap found with your request')
        else:
            match_strength = None                 # honest: nothing to measure
            best = max(sp, key=_strength_of) if sp else None
            strength_reason = "no spaces passed your filters — not computed"

        # ---- contact routing (ownership-side, from the best space) ---------
        contact_src = (fitting[0] if fitting else best)
        contact = contact_src["contact"] if contact_src else ""
        contact_tag = ("Ownership-side contact" if "<" in contact
                       else "Inquiry via landlord site")

        # ---- ordering (internal blend — not for display) -------------------
        ordering = (ORDER_WEIGHTS["match_number"] * (match_number / (match_number + SATURATION))
                    + ORDER_WEIGHTS["specialization"] * specialization
                    + ORDER_WEIGHTS["match_strength"] * (match_strength or 0.0))

        results.append({
            "landlord": name,
            "match_number": match_number,                  # "Spaces that fit you"
            "n_available": y,
            "specialization": {                            # "Area & type expertise"
                "score": round(specialization, 3),
                "x": x, "y": y,
                "reason": spec_reason,
            },
            "match_strength": {                            # "Spaces match quality"
                "score": match_strength,
                "reason": strength_reason,
                "semantic_backend": semantic.BACKEND.split(" ")[0],
            },
            "contact": contact,
            "contact_tag": contact_tag,
            "top_spaces": [{"building": s["building"], "suite": s["suite"],
                            "score": s["score"]} for s in (fitting or sp)[:5]],
            "ordering": round(ordering, 4),
        })

    results.sort(key=lambda r: r["ordering"], reverse=True)
    return results[:top_n]


if __name__ == "__main__":
    req = TenantRequest(property_type="Office", size_min=2000, size_max=8000,
                        budget_max_psf=50, area="Midtown",
                        description="bright renovated space near grand central")
    for r in rank_landlords(req):
        s = r["specialization"]; m = r["match_strength"]
        print(f"{r['landlord']:18} fit={r['match_number']:<4} "
              f"spec={s['score']:.2f} ({s['reason']})")
        print(f"{'':18} strength={m['score']} — {m['reason']}")
