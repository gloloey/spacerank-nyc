"""
app.py — SpaceRank NYC: the product wrapper (FastAPI backend) — v3
==================================================================
ENDPOINTS
  GET /                 the search UI
  GET /api/areas        submarkets (grouped) + landlord styles + backend info
  GET /api/match        ranked spaces    (query params -> TenantRequest)
  GET /api/landlords    ranked landlords (same params)

Multi-value params: repeat ?area=...&area=... for several submarkets.
`term` ("short"/"long") is accepted and ECHOED but never scored — it exists
so a future landlord-contact flow can pass it along.

Run locally:  python -m uvicorn app:app --reload  ->  http://127.0.0.1:8000
"""

import os

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

import semantic
from landlord import rank_landlords
from matching import (AREA_GROUPS, AREA_LABELS, AREAS, STYLE_LABELS,
                      TenantRequest, rank_spaces)

app = FastAPI(title="SpaceRank NYC", version="0.6",
              description="Commercial-space matching for NYC — ranked spaces "
                          "and landlords with explainable scores.")

VALID_TERMS = {"short", "long"}


def build_request(property_type, size_min, size_max, budget, areas, q,
                  landlord_style, term):
    """One place where HTTP query params become a TenantRequest.
    TenantRequest itself sanitizes nonsense (budget<=0, swapped sizes...)."""
    return TenantRequest(
        property_type=property_type,
        size_min=size_min, size_max=size_max,
        budget_max_psf=budget,
        areas=areas or [],
        description=q or "",
        landlord_style=landlord_style or None,
        term=term if term in VALID_TERMS else None,
    )


@app.get("/")
def home():
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "static", "index.html"),
                      os.path.join(here, "index.html")):
        if os.path.exists(candidate):
            return FileResponse(candidate)


@app.get("/api/areas")
def areas():
    return {"areas": sorted(AREAS),
            "groups": {g: [{"key": k, "label": AREA_LABELS[k]} for k in ks]
                       for g, ks in AREA_GROUPS.items()},
            "styles": [{"key": k, "label": v} for k, v in STYLE_LABELS.items()],
            "semantic_backend": semantic.BACKEND}


@app.get("/api/match")
def match(property_type: str = Query("Office"),
          size_min: float | None = None, size_max: float | None = None,
          budget: float | None = None,
          area: list[str] = Query(default=[]),
          q: str = "",
          landlord_style: str | None = None,
          term: str | None = None,
          top_n: int = Query(100, le=500)):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term)
    results = rank_spaces(req, top_n=top_n)
    return {"results": results,
            "total_ranked": len(results),
            # `term` is stored/echoed for the future contact flow — by design
            # it has ZERO effect on the ranking above (tests enforce this)
            "request_echo": {"term": req.term, "areas": req.areas,
                             "landlord_style": req.landlord_style}}


@app.get("/api/landlords")
def landlords(property_type: str = Query("Office"),
              size_min: float | None = None, size_max: float | None = None,
              budget: float | None = None,
              area: list[str] = Query(default=[]),
              q: str = "",
              landlord_style: str | None = None,
              term: str | None = None,
              top_n: int = 5):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term)
    return {"results": rank_landlords(req, top_n=top_n),
            "request_echo": {"term": req.term, "areas": req.areas,
                             "landlord_style": req.landlord_style}}
