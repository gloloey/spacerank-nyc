"""
landlord.py — SpaceRank NYC: Layer 3, the landlord ranking (v3)
===============================================================
Ranks landlords on exactly THREE transparent signals (see v2 docstring
history in git). v3 additions: multi-area requests (a space is "in area"
if within radius of ANY selected submarket), landlord style profiles
(institutional / family-run) shown as a tag and used as a SMALL ordering
nudge when the tenant states a preference, and NaN-proof output.

THE THREE SIGNALS
-----------------
1. match_number   COUNT of available spaces passing the hard filters
                  (type strict; size strict when given; budget rejects only
                  KNOWN rents above budget; area strict within 0.5 mi of any
                  selected submarket, un-geocoded fails).
2. specialization (X/Y) x (X/(X+DAMP)) — share of their portfolio in the
                  tenant's area+type, damped by absolute count.
3. match_strength mean over their top-3 fitting spaces of
                  0.25*size + 0.15*budget + 0.30*geo + 0.30*semantic,
                  with the matched phrase quoted. None when nothing fits.

Ordering = 0.40*n/(n+10) + 0.25*spec + 0.35*strength (+0.04 if the landlord
matches the tenant's style preference — a nudge, not a driver). Internal
only; never displayed as a combined score.
"""

from collections import defaultdict

import semantic
from matching import (AREA_LABELS, AREAS, LANDLORD_PROFILES, STYLE_LABELS,
                      TenantRequest, nearest_geo_target, rank_spaces)

ORDER_WEIGHTS = {"match_number": 0.40, "specialization": 0.25, "match_strength": 0.35}
STRENGTH_WEIGHTS = {"size": 0.25, "budget": 0.15, "geo": 0.30, "semantic": 0.30}
# 0.5 mi ~ real NYC neighborhood scale. Must match matching.COUNT_AREA_RADIUS_MI
# (one truth, two uses, same as passes_hard_filters mirroring count_spaces) —
# the old 2 km (1.24 mi) radius was big enough that most adjacent Midtown
# submarkets (e.g. Times Square <-> Grand Central, ~0.66 mi apart) fully
# bled into each other; verified by hand against the real dataset before
# picking 0.5 mi (see HANDOFF.md for the before/after overlap counts).
AREA_RADIUS_MI = 0.5
DAMP = 5
SATURATION = 10
MATCH_TOP = 3
STYLE_ORDER_BONUS = 0.04


def _in_area(space, req):
    """Within AREA_RADIUS_MI of ANY selected submarket OR the custom anchor
    point — a drawn-radius anchor uses its OWN chosen radius instead of
    AREA_RADIUS_MI. Unknown coords fail (we never claim a location we
    can't verify). No areas and no anchor -> True."""
    if not req.areas and not req.anchor:
        return True
    lat, lng = space.get("lat"), space.get("lng")
    if lat is None or lng is None:
        return False
    d, _label, custom_radius = nearest_geo_target(lat, lng, req.areas, req.anchor)
    radius = custom_radius if custom_radius is not None else AREA_RADIUS_MI
    return d is not None and d <= radius


def passes_hard_filters(space, req):
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
    sig = space["signals"]
    w = STRENGTH_WEIGHTS
    return (w["size"] * sig["size"] + w["budget"] * sig["budget"]
            + w["geo"] * sig["geo"] + w["semantic"] * sig["semantic"])


def _areas_label(req):
    """'SoHo & NoHo / Tribeca' (max 2 shown, then '+n more')."""
    labels = [AREA_LABELS[k] for k in req.areas]
    if not labels:
        return ""
    shown = " / ".join(labels[:2])
    if len(labels) > 2:
        shown += f" (+{len(labels) - 2} more)"
    return shown + " "


def rank_landlords(req: TenantRequest, top_n: int = 5,
                   csv_path: str | None = None):
    spaces = rank_spaces(req, top_n=10**9, csv_path=csv_path)

    by_landlord = defaultdict(list)
    for s in spaces:
        by_landlord[s["landlord"]].append(s)

    area_label = _areas_label(req)
    results = []
    for name, sp in by_landlord.items():
        fitting = [s for s in sp if passes_hard_filters(s, req)]

        # ---- signal 1: match_number ----------------------------------------
        match_number = len(fitting)

        # ---- signal 2: specialization --------------------------------------
        y = len(sp)
        x = sum(1 for s in sp
                if s["signals"]["type"] == 1.0 and _in_area(s, req))
        specialization = (x / y) * (x / (x + DAMP)) if y else 0.0
        spec_reason = (f"{x} of their {y} available spaces are "
                       f"{area_label}{req.property_type}")

        # ---- signal 3: match_strength --------------------------------------
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
                                   else 'no description overlap found with your request')
        else:
            match_strength = None
            best = max(sp, key=_strength_of) if sp else None
            strength_reason = "no spaces passed your filters — not computed"

        # ---- contact + profile ----------------------------------------------
        contact_src = (fitting[0] if fitting else best)
        contact = contact_src["contact"] if contact_src else ""
        contact_tag = ("Ownership-side contact" if "<" in contact
                       else "Inquiry via landlord site")
        style = LANDLORD_PROFILES.get(name)
        style_match = bool(req.landlord_style and style == req.landlord_style)

        # ---- ordering (internal) --------------------------------------------
        ordering = (ORDER_WEIGHTS["match_number"] * (match_number / (match_number + SATURATION))
                    + ORDER_WEIGHTS["specialization"] * specialization
                    + ORDER_WEIGHTS["match_strength"] * (match_strength or 0.0)
                    + (STYLE_ORDER_BONUS if style_match else 0.0))

        results.append({
            "landlord": name,
            "style": style,
            "style_label": STYLE_LABELS.get(style, ""),
            "style_match": style_match,
            "match_number": match_number,
            "n_available": y,
            "specialization": {"score": round(specialization, 3),
                               "x": x, "y": y, "reason": spec_reason},
            "match_strength": {"score": match_strength,
                               "reason": strength_reason,
                               "semantic_backend": semantic.BACKEND.split(" ")[0]},
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
                        budget_max_psf=50,
                        areas=["penn district & garment", "times square & theater district"],
                        landlord_style="family-run",
                        description="bright renovated space near transit")
    for r in rank_landlords(req):
        s = r["specialization"]; m = r["match_strength"]
        star = " *style match*" if r["style_match"] else ""
        print(f"{r['landlord']:18} [{r['style_label']}]{star} fit={r['match_number']:<4}")
        print(f"{'':18} spec={s['score']:.2f} ({s['reason']})")
        print(f"{'':18} strength={m['score']} — {m['reason']}")
