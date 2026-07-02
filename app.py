"""
app.py — SpaceRank NYC: the product wrapper (FastAPI backend)
=============================================================
Wraps the matching engine in a small web API and serves the tenant-facing
front end. FastAPI was chosen because it turns plain Python functions into
documented HTTP endpoints with almost no ceremony — visit /docs for the
auto-generated, clickable API documentation.

ENDPOINTS
  GET /                 the search UI (static/index.html)
  GET /api/areas        area names the geo scorer understands (for the dropdown)
  GET /api/match        ranked spaces    (query params -> TenantRequest)
  GET /api/landlords    ranked landlords (same params)

Run locally:
  python -m pip install fastapi uvicorn
  python -m uvicorn app:app --reload
  -> open http://127.0.0.1:8000
"""

import os

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

import semantic
from landlord import rank_landlords
from matching import AREAS, TenantRequest, rank_spaces

app = FastAPI(title="SpaceRank NYC", version="0.1",
              description="Commercial-space matching for NYC — ranked spaces "
                          "and landlords with explainable scores.")


def build_request(property_type, size_min, size_max, budget, area, q):
    """One place where HTTP query params become a TenantRequest."""
    return TenantRequest(
        property_type=property_type,
        size_min=size_min, size_max=size_max,
        budget_max_psf=budget,
        area=area or None,
        description=q or "",
    )


@app.get("/")
def home():
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "static", "index.html"),
                      os.path.join(here, "index.html")):   # deployed flat layout
        if os.path.exists(candidate):
            return FileResponse(candidate)


@app.get("/api/areas")
def areas():
    return {"areas": sorted(AREAS), "semantic_backend": semantic.BACKEND}


@app.get("/api/match")
def match(property_type: str = Query("Office"),
          size_min: float | None = None, size_max: float | None = None,
          budget: float | None = None, area: str | None = None,
          q: str = "", top_n: int = 8):
    req = build_request(property_type, size_min, size_max, budget, area, q)
    return {"results": rank_spaces(req, top_n=top_n)}


@app.get("/api/landlords")
def landlords(property_type: str = Query("Office"),
              size_min: float | None = None, size_max: float | None = None,
              budget: float | None = None, area: str | None = None,
              q: str = "", top_n: int = 5):
    req = build_request(property_type, size_min, size_max, budget, area, q)
    return {"results": rank_landlords(req, top_n=top_n)}
