"""
app.py — SpaceRank NYC: the product wrapper (FastAPI backend) — v3
==================================================================
ENDPOINTS
  GET /                    the search UI
  GET /api/areas           submarkets (grouped) + landlord styles + backend info
  GET /api/match           ranked spaces    (query params -> TenantRequest)
  GET /api/landlords       ranked landlords (same params)
  GET /api/subway-stations typeahead search over real MTA station complexes
  GET /api/geocode         resolve a free-text address to lat/lng (NYC GeoSearch)

Multi-value params: repeat ?area=...&area=... for several submarkets.
`term` ("short"/"long") is accepted and ECHOED but never scored — it exists
so a future landlord-contact flow can pass it along.

The "near a place" feature (subway station or custom address) resolves to
a plain lat/lng/label on the CLIENT side via /api/subway-stations or
/api/geocode, then that lat/lng/label is sent as anchor_lat/anchor_lng/
anchor_label on /api/match, /api/count, /api/landlords — never re-geocoded
per search. That keeps match/count fast, deterministic, and shareable-by-URL
like everything else here.

Run locally:  python -m uvicorn app:app --reload  ->  http://127.0.0.1:8000
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

import price_model as _pm
import semantic
from landlord import rank_landlords
from matching import (AREA_GROUPS, AREA_LABELS, AREAS, FIT_LABELS,
                      STYLE_LABELS, TenantRequest, count_spaces, rank_spaces,
                      search_subway_stations)

app = FastAPI(title="SpaceRank NYC", version="0.7",
              description="Commercial-space matching for NYC — ranked spaces "
                          "and landlords with explainable scores.")

VALID_TERMS = {"short", "long"}

HERE = os.path.dirname(os.path.abspath(__file__))


def dataset_meta():
    """Freshness stamp written by clean_dataset.py. {} if absent — the UI
    simply hides the stamp rather than showing a made-up date."""
    try:
        with open(os.path.join(HERE, "dataset_meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_request(property_type, size_min, size_max, budget, areas, q,
                  landlord_style, term, fit=None,
                  anchor_lat=None, anchor_lng=None, anchor_label=None):
    """One place where HTTP query params become a TenantRequest.
    TenantRequest itself sanitizes nonsense (budget<=0, swapped sizes,
    a bogus anchor point off the edge of the map...)."""
    anchor = None
    if anchor_lat is not None and anchor_lng is not None:
        anchor = {"lat": anchor_lat, "lng": anchor_lng, "label": anchor_label}
    return TenantRequest(
        property_type=property_type,
        size_min=size_min, size_max=size_max,
        budget_max_psf=budget,
        areas=areas or [],
        description=q or "",
        landlord_style=landlord_style or None,
        term=term if term in VALID_TERMS else None,
        fit_preference=fit or None,
        anchor=anchor,
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
            "fit_conditions": [{"key": k, "label": v} for k, v in FIT_LABELS.items()],
            "semantic_backend": semantic.BACKEND,
            "dataset": dataset_meta(),
            "price_model": ({"n_train": _m["n_train"], "loo_mae": _m["loo_mae"],
                             "mean_rent": _m["mean_rent"], "trained_at": _m["trained_at"]}
                            if (_m := _pm.load()) else None)}


_GEOCODE_CACHE: dict = {}


@app.get("/api/subway-stations")
def subway_stations(q: str = Query("", min_length=0, max_length=80)):
    """Typeahead search over a real, committed snapshot of MTA station
    complexes (see tools/build_subway_stations.py) — used to anchor a
    search on "near Union Sq" etc. Returns [] for a query under 2 chars
    rather than dumping all ~445 stations on the client."""
    return {"results": [{"id": s["id"], "name": s["name"],
                        "borough": s["borough"], "routes": s["routes"],
                        "lat": s["lat"], "lng": s["lng"]}
                       for s in search_subway_stations(q)]}


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=3, max_length=200)):
    """Resolve a free-text address (e.g. "350 Fifth Avenue, New York") to a
    lat/lng via NYC GeoSearch — the SAME public geocoder the scrapers use,
    just called live for a tenant-typed address instead of at scrape time.
    Small in-memory cache: identical addresses within one server lifetime
    don't re-hit the upstream API."""
    key = q.strip().lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    result = {"found": False}
    try:
        r = requests.get("https://geosearch.planninglabs.nyc/v2/search",
                         params={"size": 1, "text": q}, timeout=8)
        feats = r.json().get("features", [])
        if feats:
            lng, lat = feats[0]["geometry"]["coordinates"]
            label = feats[0]["properties"].get("label", q.strip())
            if 40.4 < lat < 41.1 and -74.35 < lng < -73.55:   # NYC-metro only
                result = {"found": True, "lat": lat, "lng": lng, "label": label}
    except Exception:
        pass   # offline/upstream hiccup -> honestly "not found", never a guess
    _GEOCODE_CACHE[key] = result
    return result


@app.get("/api/count")
def count(property_type: str = Query("Office"),
          size_min: float | None = None, size_max: float | None = None,
          budget: float | None = None,
          area: list[str] = Query(default=[]),
          anchor_lat: float | None = None, anchor_lng: float | None = None,
          anchor_label: str | None = None):
    """Live preview for the search button: spaces passing the HARD filters.
    Style / fit / term / free text are ranking inputs, never filters — by
    design they aren't even parameters here. The custom anchor point DOES
    filter, same as area, since it's a location constraint."""
    req = build_request(property_type, size_min, size_max, budget, area, "", None, None,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label)
    return count_spaces(req)


@app.get("/api/match")
def match(property_type: str = Query("Office"),
          size_min: float | None = None, size_max: float | None = None,
          budget: float | None = None,
          area: list[str] = Query(default=[]),
          q: str = "",
          landlord_style: str | None = None,
          term: str | None = None,
          fit: str | None = None,
          anchor_lat: float | None = None, anchor_lng: float | None = None,
          anchor_label: str | None = None,
          top_n: int = Query(100, le=500)):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term, fit=fit,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label)
    results = rank_spaces(req, top_n=top_n)
    return {"results": results,
            "total_ranked": len(results),
            # `term` is stored/echoed for the future contact flow — by design
            # it has ZERO effect on the ranking above (tests enforce this)
            "request_echo": {"term": req.term, "areas": req.areas,
                             "landlord_style": req.landlord_style,
                             "fit_preference": req.fit_preference,
                             "anchor": req.anchor}}


@app.get("/api/landlords")
def landlords(property_type: str = Query("Office"),
              size_min: float | None = None, size_max: float | None = None,
              budget: float | None = None,
              area: list[str] = Query(default=[]),
              q: str = "",
              landlord_style: str | None = None,
              term: str | None = None,
              fit: str | None = None,
              anchor_lat: float | None = None, anchor_lng: float | None = None,
              anchor_label: str | None = None,
              top_n: int = 5):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term, fit=fit,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label)
    return {"results": rank_landlords(req, top_n=top_n),
            "request_echo": {"term": req.term, "areas": req.areas,
                             "landlord_style": req.landlord_style,
                             "fit_preference": req.fit_preference,
                             "anchor": req.anchor}}


# ---------------------------------------------------------------------------
# Lead capture — POST /api/leads
# ---------------------------------------------------------------------------
# HONEST PERSISTENCE NOTE: this deployment is serverless (Vercel functions
# have no writable durable disk) and this project stores no secrets, so leads
# are emitted as one structured JSON line to stdout — retrievable in the
# Vercel dashboard under Logs (search "SPACERANK_LEAD"). The UI also offers
# a prefilled email fallback so no inquiry can be lost. Durable storage
# (Vercel KV / Postgres) is the documented next step and needs a credential.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class Lead(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=254)
    company: str = Field(default="", max_length=160)
    message: str = Field(default="", max_length=2000)
    interested_in: str = Field(default="", max_length=300)   # building or landlord
    landlord: str = Field(default="", max_length=120)
    search: dict = Field(default_factory=dict)               # the tenant's request

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v

    @field_validator("name", "company", "message", "interested_in", "landlord")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("search")
    @classmethod
    def _small_search(cls, v: dict) -> dict:
        # bound the echoed search so a hostile client can't log megabytes
        return {str(k)[:40]: str(val)[:200] for k, val in list(v.items())[:12]}


@app.post("/api/leads", status_code=201)
def create_lead(lead: Lead):
    if not lead.name or not lead.email:
        raise HTTPException(422, "name and email are required")
    record = {"kind": "SPACERANK_LEAD",
              "lead_id": uuid.uuid4().hex[:12],
              "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              **lead.model_dump()}
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)
    return {"ok": True, "lead_id": record["lead_id"],
            "message": "Request received — the leasing contact will hear from us.",
            "stored": "structured log (serverless — durable DB is the documented next step)"}
