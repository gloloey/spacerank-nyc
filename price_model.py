"""
price_model.py — SpaceRank NYC: honest rent estimation (v0.11)
==============================================================
92% of the market publishes "Upon request" instead of a rent. This module
trains a small, fully-explainable model on the rents that ARE published and
produces range estimates for the rest — under strict honesty rules:

  RULE 1  Estimates are INFORMATIONAL ONLY. They never enter the ranking,
          the hard filters, or the live count (test-enforced). The engine
          keeps scoring unknown rent as neutral 0.5.
  RULE 2  The model only speaks where it has seen data: a space gets an
          estimate only if its features sit inside the TRAINING ENVELOPE
          (per-feature range of the training set, with small tolerance).
          A from-GFP-lofts model must not price the Empire State Building.
  RULE 3  Every estimate is a RANGE, not a number — the band comes from
          real leave-one-out residuals (10th..90th percentile), so it is
          as wide as the model is actually wrong.
  RULE 4  The model self-reports (n_train, LOO MAE, coverage) and refuses
          to ship at all if n_train < 25 or LOO MAE > 30% of the mean rent.

WHY THIS MODEL (the interview answer)
-------------------------------------
* Ridge regression, closed form:  w = (XᵀX + λI)⁻¹ Xᵀy  — with ~33 training
  rows and 6 features, anything fancier memorizes noise. Ridge's λ shrinks
  coefficients toward zero, which is exactly what a tiny sample needs.
* Leave-one-out CV: with n this small, k-fold wastes data; LOO gives an
  almost-unbiased error estimate and is cheap at this size (n fits of a
  6x6 system). λ is chosen by LOO grid search — no hand-tuning.
* Features are building fundamentals only: log(size), building age, floors,
  distance to two anchor centroids (Plaza district = prime Midtown,
  Union Sq = prime Midtown South). Landlord identity is deliberately
  EXCLUDED — it would leak "GFP prices low" instead of learning why.
* Near-constant features are dropped automatically (a single-landlord
  training set makes e.g. is_manhattan constant — keeping it would make
  XᵀX ill-conditioned for nothing).

Artifacts: price_model.json — coefficients, standardization params,
envelope, residual band, metrics. Train on CI (fresh data weekly);
serve by evaluating one dot product per space.
"""

import json
import math
import os

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))

PLAZA = (40.7625, -73.9722)          # prime Midtown anchor
UNION_SQ = (40.7379, -73.9903)       # prime Midtown South anchor
LAMBDAS = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0]
MIN_TRAIN = 25                       # RULE 4
MAX_REL_MAE = 0.30                   # RULE 4
ENVELOPE_TOL = 0.10                  # RULE 2: 10% slack per feature range

FEATURES = ["log_size", "age", "floors", "dist_plaza_km", "dist_union_km"]


def _hav_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _features(row, med):
    """One space -> feature vector. `med` holds training medians for
    imputation (unknown year/floors get the median, never a guess of 0)."""
    size = row.get("size_sqft")
    lat, lng = row.get("lat"), row.get("lng")
    year = row.get("year_built")
    floors = row.get("floors")
    if size is None or size != size or not size or lat is None or lat != lat:
        return None                          # size + location are required
    return [
        math.log1p(float(size)),
        (2026 - float(year)) if year == year and year else med["age"],
        float(floors) if floors == floors and floors else med["floors"],
        _hav_km(lat, lng, *PLAZA),
        _hav_km(lat, lng, *UNION_SQ),
    ]


def train(csv_path=None):
    csv_path = csv_path or os.path.join(_HERE, "spaces_clean.csv")
    df = pd.read_csv(csv_path)
    df = df[df["is_available"] & (df["building_use"] == "commercial")]
    # Office only: retail rents vary block-by-block by 5-10x — a model this
    # small would be confidently wrong. (RULE 2 in spirit.)
    df = df[df["space_type"].str.contains("Office", na=False)]
    known = df[df["rent_psf"].notna() & df["lat"].notna() & df["size_sqft"].notna()]

    med = {"age": float((2026 - known["year_built"]).median()) if known["year_built"].notna().any() else 75.0,
           "floors": float(known["floors"].median()) if known["floors"].notna().any() else 12.0}

    X_rows, y = [], []
    for _, r in known.iterrows():
        f = _features(r, med)
        if f is not None:
            X_rows.append(f)
            y.append(float(r["rent_psf"]))
    n = len(y)
    if n < MIN_TRAIN:
        return {"ok": False, "reason": f"only {n} usable published rents "
                                       f"(need {MIN_TRAIN}) — no estimates shipped",
                "n_train": n}

    X = np.array(X_rows)
    y = np.array(y)

    # drop near-constant columns (single-landlord data makes some collapse)
    keep = [j for j in range(X.shape[1]) if X[:, j].std() > 1e-9]
    names = [FEATURES[j] for j in keep]
    X = X[:, keep]

    mu, sd = X.mean(axis=0), X.std(axis=0)
    Xs = (X - mu) / sd
    Xs = np.hstack([np.ones((n, 1)), Xs])            # intercept column

    def fit(A, b, lam):
        p = A.shape[1]
        reg = lam * np.eye(p)
        reg[0, 0] = 0.0                              # never shrink the intercept
        return np.linalg.solve(A.T @ A + reg, A.T @ b)

    # -- leave-one-out CV over the λ grid --------------------------------
    best = None
    for lam in LAMBDAS:
        errs = []
        for i in range(n):
            m = np.ones(n, dtype=bool); m[i] = False
            w = fit(Xs[m], y[m], lam)
            errs.append(y[i] - float(Xs[i] @ w))
        mae = float(np.mean(np.abs(errs)))
        if best is None or mae < best["mae"]:
            best = {"lam": lam, "mae": mae, "errs": errs}

    rel = best["mae"] / float(y.mean())
    ok = rel <= MAX_REL_MAE
    w = fit(Xs, y, best["lam"])

    # RULE 3: the band is the model's real LOO error distribution
    lo_q, hi_q = np.percentile(best["errs"], [10, 90])

    model = {
        "ok": bool(ok),
        "reason": None if ok else f"LOO MAE ${best['mae']:.0f} is {rel:.0%} of the "
                                  f"mean rent (limit {MAX_REL_MAE:.0%}) — not shipped",
        "trained_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_train": n,
        "lambda": best["lam"],
        "loo_mae": round(best["mae"], 2),
        "rel_mae": round(rel, 3),
        "mean_rent": round(float(y.mean()), 2),
        "features": names,
        "weights": [round(float(v), 6) for v in w],
        "mu": [round(float(v), 6) for v in mu],
        "sd": [round(float(v), 6) for v in sd],
        "medians": med,
        "band": [round(float(lo_q), 2), round(float(hi_q), 2)],
        # RULE 2: applicability envelope (per-feature training range ± tol)
        "envelope": {names[j]: [float(X[:, j].min() - ENVELOPE_TOL * (np.ptp(X[:, j]) or 1)),
                                float(X[:, j].max() + ENVELOPE_TOL * (np.ptp(X[:, j]) or 1))]
                     for j in range(len(names))},
    }
    return model


def load(path=None):
    path = path or os.path.join(_HERE, "price_model.json")
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        return m if m.get("ok") else None
    except (OSError, ValueError):
        return None


def estimate(model, row):
    """Range estimate for one space dict, or None. Applies RULE 2 (envelope)
    and only speaks about Office spaces with size + verified location."""
    if not model or "Office" not in str(row.get("space_type", "Office")):
        return None
    f_all = _features(row, model["medians"])
    if f_all is None:
        return None
    idx = {name: FEATURES.index(name) for name in model["features"]}
    f = [f_all[idx[name]] for name in model["features"]]
    for j, name in enumerate(model["features"]):        # RULE 2
        lo, hi = model["envelope"][name]
        if not (lo <= f[j] <= hi):
            return None
    z = [1.0] + [(f[j] - model["mu"][j]) / model["sd"][j] for j in range(len(f))]
    pred = float(np.dot(z, model["weights"]))
    lo, hi = pred + model["band"][0], pred + model["band"][1]
    if pred < 10 or pred > 400:                          # physical sanity
        return None
    return {"psf": round(pred), "low": round(max(lo, 5)), "high": round(hi),
            "label": f"Est. ${round(max(lo, 5))}–{round(hi)}/SF/yr"}


if __name__ == "__main__":
    m = train()
    with open(os.path.join(_HERE, "price_model.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)
    if m.get("ok"):
        print(f"trained on {m['n_train']} published rents | λ={m['lambda']} | "
              f"LOO MAE ${m['loo_mae']} ({m['rel_mae']:.0%} of mean ${m['mean_rent']})")
        print("weights:", dict(zip(["intercept"] + m["features"], m["weights"])))
        print("band: ", m["band"])
    else:
        print("MODEL NOT SHIPPED:", m.get("reason"))
