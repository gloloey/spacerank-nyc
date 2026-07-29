"""
scrape_paramount.py — landlord #10: Paramount Group (pgre.com)
================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * Paramount's site (paramount-group.com -> pgre.com) is a React SPA
    (server returns an empty <div id="app">) fed by a PUBLIC, unauthenticated
    JSON REST API on api-paramount-group.reol.com — no HTML parsing needed
    at all for the listing data, just requests+json().
  * GET /api/portfolio returns every market (NY + SF) as a list of
    {name: market_name, properties: [{property: {id, name, address, city,
    state_code, longitude, latitude, is_sold}, main_image, ...}]}. We keep
    only market == "New York" and is_sold == false.
  * GET /api/properties/{id} returns the full detail: overview (HTML
    string — description), main_image, leasing_agents (real named staff
    with @pgre.com emails), and the real prize: availabilities[].spaces[]
    = [{floor, sqft, size_type, date_available, floor_plan (PDF)}].
    IMPORTANT: the top-level available_space_sqft field on both endpoints
    is stale/unreliable (reads 0 even when spaces[] has real rows) —
    always read the nested availabilities[].spaces[] array, never that
    summary field.
  * Coordinates and photos are both embedded directly in the JSON — no
    geocoding, no lazy-load gotcha (CDN URLs are immediately real).
  * No rents published anywhere in the API -> "Upon request", honestly.
  * `listing_url` (a VTS marketplace link) and `floor_plan` (a PDF) exist
    per space but are NOT followed — the property-detail JSON already has
    everything this project's schema needs.

Paramount Group is a public REIT (NYSE: PGRE) -> landlord_style
"institutional".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import html
import re
import time
from datetime import datetime, timezone

import requests

API = "https://api-paramount-group.reol.com/api"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get_json(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_floor_code(code):
    """Paramount encodes floor as a 4-char code, mostly digits*100
    (e.g. "0200" -> floor 2, "4700" -> floor 47 — confirmed by cross-
    checking against real building heights; naively stripping leading
    zeros instead would misread "0200" as floor 200 and "2000" as floor
    20 vs floor 2 — a real bug caught by spot-checking output, not by
    trusting the recon report at face value). "RETL" = retail. A rare
    trailing-letter code like "800S" keeps the letter as a wing suffix."""
    if not code:
        return "", "Office"
    if code == "RETL":
        return "Retail", "Retail"
    digits = "".join(c for c in code if c.isdigit())
    suffix = "".join(c for c in code if c.isalpha())
    if not digits:
        return code, "Office"
    floor_num = int(digits) // 100 if len(digits) >= 3 else int(digits)
    return f"Floor {floor_num}{suffix}", "Office"


def clean_html(s):
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", " ", s)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:1500]


def nyc_property_ids():
    markets = get_json(f"{API}/portfolio")
    out = []
    for m in markets:
        if m.get("name") != "New York":
            continue
        for entry in m.get("properties", []):
            p = entry.get("property", {})
            if not p.get("is_sold"):
                out.append(p["id"])
    return out


def main():
    ids = nyc_property_ids()
    print(f"{len(ids)} Paramount Group NYC properties")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_rows = []
    for pid in ids:
        try:
            d = get_json(f"{API}/properties/{pid}")
        except Exception as e:
            print(f"  !! property {pid}: {type(e).__name__} {e}")
            continue

        p = d.get("property", {})
        name = p.get("name", "")
        address = p.get("address") or name
        lat, lng = p.get("latitude", ""), p.get("longitude", "")
        desc = clean_html(d.get("overview"))
        image_url = d.get("main_image") or ""
        source_url = f"https://www.pgre.com/properties/{pid}"

        c_role = c_name = c_email = c_phone = ""
        for agent in d.get("leasing_agents") or []:
            if agent.get("email"):
                c_role = "Leasing"
                c_name = agent.get("name", "")
                c_email = agent.get("email", "")
                c_phone = agent.get("phone", "")
                break

        base_row = {
            "landlord": "Paramount Group", "building_name": name, "address": address,
            "description": desc, "contact_role": c_role, "contact_name": c_name,
            "contact_email": c_email, "contact_phone": c_phone,
            "source_url": source_url, "scraped_at": scraped_at, "neighborhood": "",
            "lat": lat, "lng": lng, "image_url": image_url,
        }

        rows = []
        for avail in d.get("availabilities") or []:
            for space in avail.get("spaces") or []:
                sqft = space.get("sqft") or ""
                floor_label, space_type = parse_floor_code(space.get("floor", ""))
                comp = space.get("size_type", "")
                floor_suite = f"{comp} {floor_label}".strip() if comp and space_type != "Retail" else floor_label
                rows.append({**base_row, "space_type": space_type,
                            "floor_suite": floor_suite, "size_sqft": sqft,
                            "rent": "Upon request"})
        if not rows:
            rows.append({**base_row, "space_type": "Office",
                        "floor_suite": "", "size_sqft": "", "rent": ""})
        all_rows.extend(rows)
        print(f"  {name[:40]:40s} {sum(1 for r in rows if r['floor_suite'])} spaces")

    with open("data/raw/paramount_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote paramount_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
