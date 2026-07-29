"""
scrape_twotrees.py — landlord #14: Two Trees Management (twotreesny.com)
==========================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * The public site is a JS-rendered React SPA shell, but it's fed by a
    same-origin JSON API that curl hits directly with no JS execution:
      GET https://www.twotreesny.com/api/nestio?cacheBuster=<n>
    Response shape: {"data": {"1": {"label": "residential", ...},
    "2": {"label": "commercial", "data": "<JSON-encoded STRING>"}}} — the
    commercial group's `data` value is itself a JSON string and must be
    parsed a second time (`json.loads` twice), confirmed by hand before
    trusting it in code.
  * The double-parsed commercial payload is {"total_items": N, "items":
    [...]} — this is a direct passthrough of Two Trees' Nestio leasing-CRM
    feed, one object per unit: unit_number, square_footage, description,
    street_address, building {name, street_address, city, location:
    {latitude, longitude}}, photos[] (real CDN URLs), and TWO possible
    contact sources: a top-level `contacts[]` (real named staff, e.g.
    Elizabeth Bueno <ebueno@twotreesny.com> — populated on only ~2 of 19
    units checked) and `listing_company` (always populated: "Two Trees
    Commercial", a phone number, no email — used as an honest fallback
    contact when `contacts` is empty, never inventing a person).
  * `floor` is null on every item (Two Trees doesn't populate it) — the
    `unit_number` field is the only per-space identifier available and is
    used as floor_suite as-is, never guessed at.
  * Coordinates: building.location.{latitude,longitude} are embedded
    exactly -> no geocoding needed.
  * Photo: photos[].original is a real, immediately-usable CDN URL
    (assets-img.nestiostatic.com) — no lazy-load placeholder, no data: URI.
  * Rents: `price` is null on every commercial item (the residential group
    DOES publish numeric price — confirmed by hand this is a group-level
    distinction, not a bug) -> "Upon request", honestly.
  * IMPORTANT LIMITATION: this feed only ever returns commercial_use ==
    "Office" — Two Trees' retail spaces (/retail-spaces) are a static
    marketing page with no equivalent structured feed, so retail is simply
    not captured here rather than guessed at from prose.

Two Trees Management was founded by David Walentas and is currently led
by his son Jed Walentas as CEO — a citable, publicly documented two-
generation family firm -> landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import json
import re
import time
from datetime import datetime, timezone

import requests

API = "https://www.twotreesny.com/api/nestio"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def main():
    r = requests.get(API, headers=HEADERS, params={"cacheBuster": 1}, timeout=30)
    r.raise_for_status()
    outer = r.json()
    commercial = json.loads(outer["data"]["2"]["data"])
    items = commercial.get("items", [])
    print(f"{len(items)} Two Trees commercial units (office only — see docstring)")

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for it in items:
        building = it.get("building") or {}
        loc = building.get("location") or {}
        name = building.get("name") or it.get("street_address", "")
        address = f"{it.get('street_address', name)}, {building.get('city', 'Brooklyn')}, NY"

        contacts = it.get("contacts") or []
        if contacts:
            c = contacts[0]
            c_name, c_email, c_phone = c.get("name", ""), c.get("email", ""), c.get("phone_number", "")
        else:
            lc = it.get("listing_company") or {}
            c_name, c_email, c_phone = lc.get("name", ""), lc.get("email", ""), lc.get("phone_number", "")

        photos = it.get("photos") or []
        image_url = photos[0].get("original", "") if photos else ""

        unit = it.get("unit_number") or ""
        floor_suite = f"Suite {unit}" if unit else "Space"

        rows.append({
            "landlord": "Two Trees Management", "building_name": name, "address": address,
            "description": re.sub(r"\s+", " ", it.get("description") or "").strip()[:1200],
            "space_type": "Office", "floor_suite": floor_suite,
            "size_sqft": it.get("square_footage") or "",
            "rent": "Upon request",
            "contact_role": "Leasing" if (c_email or c_name) else "",
            "contact_name": c_name, "contact_email": c_email, "contact_phone": c_phone,
            "source_url": "https://www.twotreesny.com/office-spaces",
            "scraped_at": scraped_at, "neighborhood": (building.get("neighborhood") or {}).get("name", ""),
            "lat": loc.get("latitude", ""), "lng": loc.get("longitude", ""),
            "image_url": image_url,
        })
        print(f"  {name[:34]:34s} {floor_suite[:16]:16s} {it.get('square_footage')} sf")

    with open("data/raw/twotrees_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote twotrees_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
