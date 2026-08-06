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
  GET /api/geocode-suggest live multi-result address suggestions as-you-type
  POST /api/leads          capture a tenant inquiry (log + Postgres + email if configured)
  GET /admin                small dashboard for the two endpoints below
  GET /api/admin/leads      [ADMIN_API_KEY] captured leads
  GET /api/admin/stats      [ADMIN_API_KEY] search/conversion analytics

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
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

import db
import email_notify
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
                  anchor_lat=None, anchor_lng=None, anchor_label=None,
                  anchor_radius_mi=None):
    """One place where HTTP query params become a TenantRequest.
    TenantRequest itself sanitizes nonsense (budget<=0, swapped sizes,
    a bogus anchor point off the edge of the map, a bogus radius...)."""
    anchor = None
    if anchor_lat is not None and anchor_lng is not None:
        anchor = {"lat": anchor_lat, "lng": anchor_lng, "label": anchor_label}
        if anchor_radius_mi is not None:
            anchor["radius_mi"] = anchor_radius_mi
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


@app.get("/admin")
def admin_page():
    here = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(here, "admin.html"))


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


_SUGGEST_CACHE: dict = {}


@app.get("/api/geocode-suggest")
def geocode_suggest(q: str = Query(..., min_length=3, max_length=200)):
    """Live address suggestions as the tenant types — the /v2/autocomplete
    NYC GeoSearch endpoint (built for exactly this, unlike /v2/search's
    single best-guess match used by /api/geocode above). Each candidate
    already carries lat/lng, so picking one needs no second round-trip.

    HONEST LIMIT: NYC GeoSearch indexes streets/addresses only, not business
    or venue names ("Starbucks") — confirmed empirically, not assumed. A
    business-name query here honestly returns no results rather than a
    guessed address; there's no free, keyless NYC data source that indexes
    business names, so that's a real gap, not a bug."""
    key = q.strip().lower()
    if key in _SUGGEST_CACHE:
        return _SUGGEST_CACHE[key]
    results = []
    try:
        r = requests.get("https://geosearch.planninglabs.nyc/v2/autocomplete",
                         params={"size": 5, "text": q}, timeout=8)
        for feat in r.json().get("features", []):
            lng, lat = feat["geometry"]["coordinates"]
            if 40.4 < lat < 41.1 and -74.35 < lng < -73.55:   # NYC-metro only
                results.append({"label": feat["properties"].get("label", q.strip()),
                                "lat": lat, "lng": lng})
    except Exception:
        pass   # offline/upstream hiccup -> honestly empty, never a guess
    _SUGGEST_CACHE[key] = {"results": results}
    return _SUGGEST_CACHE[key]


@app.get("/api/count")
def count(property_type: str = Query("Office"),
          size_min: float | None = None, size_max: float | None = None,
          budget: float | None = None,
          area: list[str] = Query(default=[]),
          anchor_lat: float | None = None, anchor_lng: float | None = None,
          anchor_label: str | None = None, anchor_radius_mi: float | None = None):
    """Live preview for the search button: spaces passing the HARD filters.
    Style / fit / term / free text are ranking inputs, never filters — by
    design they aren't even parameters here. The custom anchor point DOES
    filter, same as area, since it's a location constraint."""
    req = build_request(property_type, size_min, size_max, budget, area, "", None, None,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label,
                        anchor_radius_mi=anchor_radius_mi)
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
          anchor_label: str | None = None, anchor_radius_mi: float | None = None,
          top_n: int = Query(100, le=500)):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term, fit=fit,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label,
                        anchor_radius_mi=anchor_radius_mi)
    results = rank_spaces(req, top_n=top_n)
    db.log_search_event(req, len(results))
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
              anchor_label: str | None = None, anchor_radius_mi: float | None = None,
              top_n: int = 5):
    req = build_request(property_type, size_min, size_max, budget, area, q,
                        landlord_style, term, fit=fit,
                        anchor_lat=anchor_lat, anchor_lng=anchor_lng, anchor_label=anchor_label,
                        anchor_radius_mi=anchor_radius_mi)
    return {"results": rank_landlords(req, top_n=top_n),
            "request_echo": {"term": req.term, "areas": req.areas,
                             "landlord_style": req.landlord_style,
                             "fit_preference": req.fit_preference,
                             "anchor": req.anchor}}


# ---------------------------------------------------------------------------
# Lead capture — POST /api/leads
# ---------------------------------------------------------------------------
# HONEST PERSISTENCE NOTE: leads are always emitted as one structured JSON
# line to stdout (retrievable in the Vercel dashboard under Logs, search
# "SPACERANK_LEAD") AND, when DATABASE_URL is configured, written to Postgres
# via db.insert_lead(). The log is never removed even with a DB present —
# db.insert_lead() degrades to a no-op rather than raising, so a database
# hiccup can never turn a tenant's form submission into a lost lead or a
# 500. Two notification emails (admin + tenant confirmation) fire the same
# way through email_notify.py — also a no-op without RESEND_API_KEY, never
# a 500. There's no "email the landlord directly" step here on purpose —
# not offered at launch.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
VALID_TENANT_TYPES = {"tenant", "broker", ""}


class Lead(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    phone: str = Field(default="", max_length=32)
    company: str = Field(default="", max_length=160)
    tenant_type: str = Field(default="", max_length=16)       # "tenant" | "broker" | ""
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

    @field_validator("tenant_type")
    @classmethod
    def _valid_tenant_type(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in VALID_TENANT_TYPES else ""

    @field_validator("first_name", "last_name", "phone", "company", "message",
                     "interested_in", "landlord")
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
    if not lead.first_name or not lead.last_name or not lead.email:
        raise HTTPException(422, "first name, last name and email are required")
    record = {"kind": "SPACERANK_LEAD",
              "lead_id": uuid.uuid4().hex[:12],
              "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              **lead.model_dump()}
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)
    persisted = db.insert_lead(record)
    stored = "database + structured log" if persisted else "structured log (database not configured)"
    admin_emailed = email_notify.send_admin_notification(record)
    tenant_emailed = email_notify.send_tenant_confirmation(record)
    return {"ok": True, "lead_id": record["lead_id"],
            "message": "Request received — the leasing contact will hear from us.",
            "stored": stored, "admin_notified": admin_emailed, "tenant_confirmed": tenant_emailed}


# ---------------------------------------------------------------------------
# Admin — GET /api/admin/leads, GET /api/admin/stats
# ---------------------------------------------------------------------------
# Guarded by a single shared API key (env var ADMIN_API_KEY, set by Gabriel —
# same "credential in, never hardcoded" pattern as DATABASE_URL). Fails
# CLOSED: if ADMIN_API_KEY isn't set at all, every request is rejected
# rather than the endpoint being wide open by default.

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


def _require_admin(x_admin_key: str = Header(default="")):
    if not ADMIN_API_KEY or x_admin_key != ADMIN_API_KEY:
        raise HTTPException(401, "unauthorized")


@app.get("/api/admin/leads")
def admin_leads(limit: int = Query(200, le=1000), _admin: None = Depends(_require_admin)):
    return {"leads": db.fetch_leads(limit)}


@app.get("/api/admin/stats")
def admin_stats(_admin: None = Depends(_require_admin)):
    stats = db.fetch_stats()
    if stats is None:
        raise HTTPException(503, "database not configured")
    return stats
