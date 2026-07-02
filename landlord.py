"""
landlord.py — SpaceRank NYC: Layer 3, the landlord ranking
==========================================================
Answers "which LANDLORD is best for what this tenant wants?" by rolling
space-level matches up to the landlord level.

IMPORTANT DESIGN POINT (from the project spec): this is a RELEVANCE score,
not a prediction. We are not claiming to know a hidden truth like "how
reliable is this landlord" — no ground truth exists for that. Every signal
below is real and countable, and each result says exactly why it surfaced.

THE FIVE SIGNALS (each 0..1, weights explicit):

  quality        how good are their best matches? (mean of top-3 space scores)
  depth          how MANY currently-available spaces fit well? (score >= 60)
  area presence  do they have buildings where the tenant wants to be?
  specialization what share of their available portfolio is the wanted type?
  semantic       how well do their descriptions match the tenant's free text?

Usage:
    from landlord import rank_landlords
    landlords = rank_landlords(TenantRequest(...))
"""

from collections import defaultdict

from matching import TenantRequest, rank_spaces

L_WEIGHTS = {"quality": 0.30, "depth": 0.20, "area": 0.15,
             "specialization": 0.15, "semantic": 0.20}

FIT_THRESHOLD = 60.0     # a space "fits" if its blended score is >= this


def rank_landlords(req: TenantRequest, top_n: int = 5,
                   csv_path: str | None = None):
    # Score EVERY available space once; the landlord layer is pure aggregation
    # on top of the space layer — one engine, two views of its output.
    spaces = rank_spaces(req, top_n=10**9, csv_path=csv_path)

    by_landlord = defaultdict(list)
    for s in spaces:
        by_landlord[s["landlord"]].append(s)

    results = []
    for name, sp in by_landlord.items():
        sp.sort(key=lambda s: s["score"], reverse=True)

        # quality: average of the top-3 space scores, rescaled to 0..1
        top3 = sp[:3]
        quality = sum(s["score"] for s in top3) / len(top3) / 100

        # depth: how many spaces clear the fit bar (capped at 5 -> saturates)
        n_fit = sum(1 for s in sp if s["score"] >= FIT_THRESHOLD)
        depth = min(n_fit, 5) / 5

        # area presence: their single best geo signal (1.0 = right there)
        area = max(s["signals"]["geo"] for s in sp)

        # specialization: share of their available spaces of the wanted type
        exact = sum(1 for s in sp if s["signals"]["type"] == 1.0)
        specialization = exact / len(sp)

        # semantic: mean of the top-5 description matches
        top5 = sp[:5]
        sem = sum(s["signals"]["semantic"] for s in top5) / len(top5)

        w = L_WEIGHTS
        total = (w["quality"] * quality + w["depth"] * depth + w["area"] * area
                 + w["specialization"] * specialization + w["semantic"] * sem)

        best = sp[0]
        results.append({
            "score": round(100 * total, 1),
            "landlord": name,
            "n_available": len(sp),
            "n_fitting": n_fit,
            "best_space": f"{best['building']} — {best['suite']} [{best['score']}]",
            # Contact routing: the leasing contact of their best-fitting
            # space — the most direct ownership-side person for THIS request.
            "contact": best["contact"],
            "reason": (f"{n_fit} space(s) fit your request (best: {best['score']}); "
                       f"{int(100 * specialization)}% of available portfolio is "
                       f"{req.property_type}; "
                       f"{'strong' if area >= 0.8 else 'some' if area >= 0.4 else 'weak'}"
                       f" presence in your target area"),
            "signals": {"quality": round(quality, 3), "depth": round(depth, 3),
                        "area": round(area, 3), "specialization": round(specialization, 3),
                        "semantic": round(sem, 3)},
            "top_spaces": [{"building": s["building"], "suite": s["suite"],
                            "score": s["score"]} for s in sp[:5]],
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]


if __name__ == "__main__":
    req = TenantRequest(property_type="Office", size_min=2000, size_max=6000,
                        budget_max_psf=45, area="Midtown",
                        description="professional building with renovated lobby")
    for r in rank_landlords(req):
        print(f"[{r['score']:5.1f}] {r['landlord']} — {r['reason']}")
        print(f"        best: {r['best_space']}")
        print(f"        contact: {r['contact']}")
