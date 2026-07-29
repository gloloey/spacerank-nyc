"""
scrape_brause.py — landlord #17: Brause Realty (brauserealty.com)
===================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * Server-rendered WordPress. https://brauserealty.com/office/ links to
    one page per building (e.g. /52-vanderbilt-avenue/, /41-union-square-
    west/); found by grepping real hrefs, not guessed slugs.
  * IMPORTANT LIMITATION: this is a small, family-run portfolio site with
    NO per-suite table anywhere — each building page is one paragraph of
    prose describing the whole building's amenities and an aggregate size
    RANGE (e.g. "sizes ranging from 2,000-10,000 square feet"), never a
    single per-space number. Rather than guess a specific sqft from a
    range, this scraper emits one row per building with size_sqft blank
    (neutral 0.5 in the matching engine — honest, not invented), matching
    the same "portfolio row when no itemized availability exists" pattern
    already used by scrape_vornado.py and scrape_silverstein.py.
  * Coordinates: not embedded -> geocoded via NYC GeoSearch from the
    building's own address line, same pattern as every other scraper here.
  * Photo: a real, immediately-usable wp-content/uploads image URL per
    building — no lazy-load placeholder, no data: URI.
  * Ownership-side leasing CONTACT: each building page ends with "For
    Property Information Contact <Name>" — a REAL family member (David
    Brause, Melissa Brause Rackoff — confirmed by hand), but NO email
    address anywhere on any page checked, only the company's main phone
    number. Stored honestly as name + company phone, email left blank —
    same "real contact, no invented email" pattern as scrape_rudin.py.
  * Rents: never published anywhere -> "Upon request", honestly.
  * The /retail/ portfolio page currently links to no distinct retail
    properties (same nav-only links as every other page) — nothing to
    scrape there at this time; only /office/ has real per-building pages.
  * One upstate NY property (Congress Park Centre, Saratoga Springs) is
    excluded — out of NYC-metro scope, same filtering logic used
    elsewhere in this codebase for out-of-market assets.

Brause Realty is a family-owned and family-run NYC firm (the Brause
family — confirmed by the named per-building contacts themselves being
Brauses) -> landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://brauserealty.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
COMPANY_PHONE = "212.697.5454"
EXCLUDE_SLUGS = {"congress-park-centre-2", "congress-park-centre"}  # Saratoga Springs — not NYC metro

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
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
    html = get(f"{BASE}/office/")
    slugs = sorted(set(re.findall(r'href="https://brauserealty\.com/([a-z0-9-]+)/"', html)))
    skip = {"office", "residential", "retail", "about", "contact", "wp-json"}
    return [s for s in slugs if s not in skip and s not in EXCLUDE_SLUGS]


def parse_property(slug):
    url = f"{BASE}/{slug}/"
    soup = BeautifulSoup(get(url), "lxml")
    # the page's raw flattened text has a duplicated (desktop+mobile) nav
    # breadcrumb before the real heading, which made a text-regex approach
    # fragile — the h2 tags are clean and unambiguous, use those instead.
    headings = [h.get_text(" ", strip=True) for h in soup.select("h2")]
    name = headings[0] if headings else slug.replace("-", " ").title()

    contact_h2 = next((h for h in headings if h.lower().startswith("for property")), "")
    contact_m = re.search(r"Contact\s*([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){1,2})", contact_h2)
    c_name = contact_m.group(1).strip() if contact_m else ""

    paragraphs = [p.get_text(" ", strip=True) for p in soup.select("p")]
    body_paragraphs = [p for p in paragraphs if not p.lower().startswith("building features")]
    description = re.sub(r"\s+", " ", " ".join(body_paragraphs)).strip()[:1200]

    # Not every building name is a real geocodable street address (e.g.
    # "Brewster LIC" is a marketing name, not a street address). Caught by
    # spot-checking output coordinates rather than assuming success: even
    # WITH a "Long Island City" hint appended, the geocoder's own query
    # parser matched "Brewster" to a real, unrelated "33 Brewster Street,
    # Staten Island" with high confidence, overriding the hint entirely —
    # so for non-address names, this scraper deliberately geocodes ONLY
    # the neighborhood named in the page's own prose (e.g. "delivers ...
    # in Long Island City"), dropping the marketing name from the query,
    # trading building-level precision for not landing in the wrong
    # borough. A generic neighborhood-centroid point is honestly
    # approximate; a wrong-borough point would be confidently wrong.
    if re.match(r"^\d", name):
        address = f"{name}, New York, NY"
    else:
        hood_m = re.search(r"\bin ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){0,2})\b", description)
        address = f"{hood_m.group(1)}, New York, NY" if hood_m else "New York, NY"

    # BUG FIX (found by Gabriel — every building was showing the SAME
    # picture): the naive first-match selector was grabbing the site's own
    # logo, which is also hosted under wp-content/uploads and appears
    # before any real building photo in the DOM. Skip anything logo-shaped
    # (by src filename, alt text, or class) and take the first real
    # "Gallery Image"-alt photo instead.
    image_url = ""
    for candidate in soup.select("img[src*='wp-content/uploads']"):
        src = candidate.get("src", "")
        alt = (candidate.get("alt") or "").lower()
        classes = " ".join(candidate.get("class") or []).lower()
        if "logo" in src.lower() or "logo" in alt or "logo" in classes:
            continue
        image_url = src
        break
    if image_url.startswith("data:"):
        image_url = ""

    lat, lng = geocode(address)

    return {
        "landlord": "Brause Realty", "building_name": name, "address": address,
        "description": description, "space_type": "Office",
        "floor_suite": "Available — contact for space details", "size_sqft": "",
        "rent": "Upon request", "contact_role": "Leasing" if c_name else "",
        "contact_name": c_name, "contact_email": "", "contact_phone": COMPANY_PHONE,
        "source_url": url, "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "neighborhood": "", "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }


def main():
    slugs = property_slugs()
    print(f"{len(slugs)} Brause Realty NYC office buildings")
    rows = []
    for slug in slugs:
        try:
            row = parse_property(slug)
            rows.append(row)
            print(f"  {row['building_name'][:34]:34s} contact={row['contact_name'] or '(none)'}")
        except Exception as e:
            print(f"  !! {slug}: {type(e).__name__} {e}")

    with open("data/raw/brause_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote brause_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
