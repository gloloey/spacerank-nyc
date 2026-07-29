"""
scrape_savanna.py — landlord #19: Savanna (savannafund.com)
=================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * Server-rendered WordPress. https://savannafund.com/portfolio/ links to
    one page per property (e.g. /portfolio/521-fifth-avenue/) — found by
    grepping real hrefs, not guessed slugs.
  * Building name + description come from the page's own `<meta
    property="og:title">` / `<meta property="og:description">` tags — a
    specific, isolated source, not a regex over the whole flattened page
    (that mistake already bit scrape_brause.py once).
  * IMPORTANT LIMITATION: like scrape_brause.py / scrape_vornado.py /
    scrape_silverstein.py, there is no per-suite table — each detail page
    is prose about the whole building. Every page's "See Availabilities"
    CTA (when present) links OFF-DOMAIN to marketplace.vts.com, a
    third-party leasing marketplace — per CLAUDE.md rule 3 (owner sites
    only, never brokers/marketplaces), this scraper never follows it. One
    row per building, size_sqft left blank — honest, not invented.
  * Coordinates: not embedded anywhere on the page -> geocoded via NYC
    GeoSearch. Caught by hand before trusting it: geocoding the spelled-out
    marketing name "One Court Square, Long Island City, NY" silently
    matched an unrelated "ONE MANHATTAN SQUARE" (same failure mode as
    "Brewster LIC" in scrape_brause.py — spelled-out numbers in a building
    name confuse the geocoder's fuzzy text match more than digit-form
    street addresses do). Verified the FIX by hand too: the numeral form
    "1 Court Square, Long Island City, NY" geocodes correctly. Same
    per-building verification for "The Six" (a marketing name with no
    number) — its own description names a real numbered address, "106
    West 56th Street", which geocodes correctly; used instead of the
    marketing name. Every other building name here already IS a real,
    correctly-geocoding street address (spot-checked all five by hand).
  * Photo: real, immediately-usable wp-content/uploads image URLs. The
    site's own logo lives under a DIFFERENT path
    (wp-content/themes/savanna/...), so the wp-content/uploads selector
    used here never needs a logo-skip guard the way Brause's did — still
    spot-checked by hand to confirm, not assumed.
  * Ownership-side leasing CONTACT: the shared site footer
    (`footer#footer`) has a real company email (info@savannafund.com) and
    phone (212.229.0101) at Savanna's own Park Avenue office — a real
    team, not a per-suite named person, same honesty level as
    scrape_feil.py / scrape_timeequities.py.
  * Rents: never published anywhere -> "Upon request", honestly.
  * "Vandewater" is a residential condominium (not commercial office/
    retail) and is excluded — same "this is a commercial tenant-matching
    engine, not a residential search tool" scope rule used in
    scrape_tishmanspeyer.py.

Savanna is a privately-held real-estate investment/management firm; this
scraper does not have a citable public source pinning down "family-run" in
the same verifiable sense as Rudin/Durst/GFP/Silverstein, so landlord_style
is left unclassified rather than assumed, same reasoning as RXR Realty.
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://savannafund.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8

EXCLUDE_SLUGS = {"vandewater"}  # residential condominium, out of commercial scope

# Building names that are marketing names, not real geocodable street
# addresses — hand-verified real addresses substituted (see docstring).
ADDRESS_OVERRIDES = {
    "one-court-square": "1 Court Square, Long Island City, NY",
    "the-six": "106 West 56th Street, New York, NY",
}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def geocode(text, cache={}):
    if text in cache:
        return cache[text]
    out = (None, None)
    try:
        time.sleep(0.4)
        r = requests.get("https://geosearch.planninglabs.nyc/v2/search",
                         params={"size": 1, "text": text}, timeout=10)
        feats = r.json().get("features", [])
        if feats:
            lng, lat = feats[0]["geometry"]["coordinates"]
            if 40.4 < lat < 41.1:
                out = (lat, lng)
    except Exception:
        pass
    cache[text] = out
    return out


def property_slugs():
    html = get(f"{BASE}/portfolio/")
    slugs = sorted(set(re.findall(r'href="https://savannafund\.com/portfolio/([a-z0-9-]+)/"', html)))
    return [s for s in slugs if s not in EXCLUDE_SLUGS]


def footer_contact(soup):
    a = soup.select_one("footer#footer a[href^='mailto:']")
    if not a:
        return "", ""
    email = a["href"].replace("mailto:", "").split("?")[0].strip()
    phone_m = re.search(r"(\d{3}\.\d{3}\.\d{4})", a.find_parent("div").get_text(" ", strip=True))
    return email, phone_m.group(1) if phone_m else ""


def parse_property(slug):
    url = f"{BASE}/portfolio/{slug}/"
    soup = BeautifulSoup(get(url), "lxml")

    og_title = soup.select_one('meta[property="og:title"]')
    og_desc = soup.select_one('meta[property="og:description"]')
    name = og_title["content"].split(" - Savanna")[0].strip() if og_title else slug.replace("-", " ").title()
    description = og_desc["content"].strip() if og_desc else ""
    name = re.sub(r"\s+", " ", name.replace("\xa0", " ")).strip()
    description = re.sub(r"\s+", " ", description.replace("\xa0", " ")).strip()

    address = ADDRESS_OVERRIDES.get(slug) or (f"{name}, New York, NY" if re.match(r"^\d", name) else "New York, NY")
    lat, lng = geocode(address)

    image_url = ""
    img = soup.select_one("img[src*='wp-content/uploads']")
    if img and "logo" not in (img.get("src", "") + (img.get("alt") or "")).lower():
        image_url = img.get("src", "")
    if image_url.startswith("data:"):
        image_url = ""

    c_email, c_phone = footer_contact(soup)

    return {
        "landlord": "Savanna", "building_name": name, "address": address,
        "description": description[:1200], "space_type": "Office",
        "floor_suite": "Available — contact for space details", "size_sqft": "",
        "rent": "Upon request", "contact_role": "Leasing" if c_email else "",
        "contact_name": "", "contact_email": c_email, "contact_phone": c_phone,
        "source_url": url, "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "neighborhood": "", "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }


def main():
    slugs = property_slugs()
    print(f"{len(slugs)} Savanna NYC commercial buildings")
    rows = []
    for slug in slugs:
        try:
            row = parse_property(slug)
            rows.append(row)
            print(f"  {row['building_name'][:34]:34s} lat/lng={row['lat']},{row['lng']}")
        except Exception as e:
            print(f"  !! {slug}: {type(e).__name__} {e}")

    with open("data/raw/savanna_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote savanna_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
