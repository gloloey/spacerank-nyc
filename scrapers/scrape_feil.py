"""
scrape_feil.py — landlord #13: The Feil Organization (feil.com)
=================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * The visible /all_properties/ page is a Vue-hydrated grid, but it's fed
    by a plain WordPress admin-ajax JSON endpoint — no JS execution needed:
      GET https://feil.com/wp-admin/admin-ajax.php?action=get_properties
          &security=<nonce>
      GET https://feil.com/wp-admin/admin-ajax.php?action=get_agents
          &security=<nonce>
    The nonce is a WP security token embedded in the /all_properties/ page's
    inline JS and can rotate — this scraper fetches that page first and
    regexes the current value out, rather than hardcoding it.
  * get_properties returns 178 properties nationwide (mixed office/retail/
    residential/net-lease-single-tenant, several states) — filtered here to
    property-state.slug=="ny" AND location.city in the five NYC boroughs
    AND property-type tags intersecting {office, retail, mixed, medical}
    (pure residential/net-lease-only buildings dropped). Each qualifying
    property's `availabilities[]` is a real per-space array: {space_name
    (free text, e.g. "Entire 8th Floor – Vacant" or "Suite 1100" — kept
    as-is, it's already a usable floor/suite label), space_surface_value
    (sqft), space_price_value (always "0"/blank in practice -> "Upon
    request", never invented)}.
  * Coordinates: location.lat/lng are embedded exactly -> no geocoding
    needed for Feil's NYC properties.
  * Photo: `img` field is a real, immediately-usable CDN URL (SharpLaunch-
    hosted) — no lazy-load placeholder, no data: URI.
  * Ownership-side leasing CONTACT: get_agents returns real named staff
    with @feilorg.com emails, tagged by job_title ("Office Leasing" /
    "Retail Leasing" / etc.) — CONFIRMED BY HAND: individual properties
    carry no direct agent link (`brokers` is always [] on every property
    checked), so this scraper picks the first agent whose job_title
    matches the BUILDING's own type tag (office vs retail) as an honest,
    clearly-a-team-not-a-person-specific-to-this-suite assignment, same
    spirit as a portfolio-level leasing team listing.
  * Rents: always "0"/blank in the JSON -> "Upon request", honestly.

The Feil Organization is a large, privately-held NYC real estate family
firm (founded by the Feil family, still family-led) -> landlord_style
"family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests

BASE = "https://feil.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
NYC_CITIES = {"New York", "Bronx", "Brooklyn", "Queens", "Staten Island"}
COMMERCIAL_TYPES = {"office", "retail", "mixed", "medical"}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url, params=None):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r


def find_nonce():
    html = get(f"{BASE}/all_properties/").text
    m = re.search(r"security=([a-f0-9]{8,})", html)
    if not m:
        raise RuntimeError("could not find the security nonce on /all_properties/")
    return m.group(1)


def building_space_type(property_types):
    slugs = {t.get("slug") for t in property_types or []}
    if "retail" in slugs and not (slugs & {"office", "mixed", "medical"}):
        return "Retail"
    return "Office"


def pick_agent(agents, space_type):
    want = "retail" if space_type == "Retail" else "office"
    for a in agents:
        if want in (a.get("job_title") or "").lower() and a.get("email"):
            return a["name"].strip(), a["email"], a.get("phone", "")
    return "", "", ""


def main():
    nonce = find_nonce()
    properties = get(f"{BASE}/wp-admin/admin-ajax.php",
                     params={"action": "get_properties", "security": nonce}).json()
    agents = get(f"{BASE}/wp-admin/admin-ajax.php",
                params={"action": "get_agents", "security": nonce}).json()

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for p in properties:
        if not any(s.get("slug") == "ny" for s in p.get("property-state") or []):
            continue
        loc = p.get("location") or {}
        if loc.get("city") not in NYC_CITIES:
            continue
        types = p.get("property-type") or []
        if not ({t.get("slug") for t in types} & COMMERCIAL_TYPES):
            continue
        avail = p.get("availabilities") or []
        if not avail:
            continue

        name = p.get("name", "")
        space_type = building_space_type(types)
        c_name, c_email, c_phone = pick_agent(agents, space_type)
        address = f"{loc.get('address', name)}, {loc.get('city', 'New York')}, NY"
        image_url = p.get("img") or ""
        if image_url.startswith("data:"):
            image_url = ""

        base_row = {
            "landlord": "The Feil Organization", "building_name": name, "address": address,
            "description": "", "contact_role": "Leasing" if c_email else "",
            "contact_name": c_name, "contact_email": c_email, "contact_phone": c_phone,
            "source_url": p.get("url", ""), "scraped_at": scraped_at, "neighborhood": "",
            "lat": loc.get("lat", ""), "lng": loc.get("lng", ""), "image_url": image_url,
        }
        n_spaces = 0
        for space in avail:
            sqft = space.get("space_surface_value") or ""
            price = space.get("space_price_value") or ""
            rows.append({**base_row, "space_type": space_type,
                        "floor_suite": space.get("space_name", ""), "size_sqft": sqft,
                        "rent": f"${price}/SF" if price and price != "0" else "Upon request"})
            n_spaces += 1
        print(f"  {name[:40]:40s} {space_type:7s} {n_spaces} spaces")

    with open("data/raw/feil_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote feil_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
