"""
scrape_brookfield.py — landlord #7: Brookfield Properties (brookfieldproperties.com)
=====================================================================================
ARCHITECTURE (scouted 2026-08 via curl, field names verified by hand against
a live page fetch — not guessed):
  * Property discovery: the portfolio directory page embeds a PUBLIC,
    read-only Algolia search index (window.__BP_ALGOLIA_SEARCH__). It's a
    search-only API key (safe to call directly): POST to
    https://{appId}-dsn.algolia.net/1/indexes/BP.com/query with headers
    X-Algolia-API-Key / X-Algolia-Application-Id. One query with
    hitsPerPage=1000 returns the whole sitewide index (~1000-1200 hits:
    property pages mixed with art-collection pages, leader bios, etc.).
    Each property hit has objectID like "OFF-OFF-NY-00407" (asset-type
    prefix + state + numeric id) and a "url" field — we keep only
    OFF-OFF-NY-* (office) and RET-RET-NY-* (retail) hits.
  * Each property page is server-rendered and ships its own data as an
    executable JS statement, not JSON-typed markup:
      <script>...Fusion.globalContent={...huge JSON...};Fusion.globalContentConfig=...</script>
    A non-greedy regex + json.loads() gets the whole object — no CSS
    selectors needed. Confirmed top-level fields: propertyName, fullAddress,
    latitude, longitude, sector (list, e.g. ["Office"]), descriptionHTML
    (plain text despite the name), thumbnail/images (real CDN URLs, never
    lazy-loaded), contacts (list of {role, firstName, lastName, phoneNumber,
    emailAddress} — real named Brookfield employees on their own domain),
    and vtsList (list of live availabilities: {area:{magnitude}, floor,
    suite, floorComposition, condition, available}).
  * No rents published anywhere -> "Upon request", honestly.
  * A property with an empty vtsList still contributes one portfolio row
    (matches the Vornado pattern) so the building itself is searchable even
    with zero current availabilities.

Brookfield Properties is part of Brookfield Asset Management (NYSE: BN), a
global institutional alternative-asset manager -> landlord_style
"institutional" (not itself a separately-listed public REIT like SLG/VNO/
ESRT, but honestly describable as institutionally owned/operated).
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import json
import re
import time
from datetime import datetime, timezone

import requests

BASE = "https://www.brookfieldproperties.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 1.0

ALGOLIA_URL = "https://1F70JSSJ4O-dsn.algolia.net/1/indexes/BP.com/query"
ALGOLIA_HEADERS = {
    "X-Algolia-API-Key": "8220ed65537beb20e277a92c4ff111a0",
    "X-Algolia-Application-Id": "1F70JSSJ4O",
    "Content-Type": "application/json",
}
OBJECTID_RE = re.compile(r"^(OFF-OFF|RET-RET)-NY-\d+$")

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def property_urls():
    """Enumerate NYC office/retail property page URLs via the public Algolia index."""
    resp = requests.post(ALGOLIA_URL, headers=ALGOLIA_HEADERS,
                          json={"params": "query=&hitsPerPage=1000&page=0"}, timeout=30)
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    urls = []
    for h in hits:
        oid = h.get("objectID", "")
        url = h.get("url", "")
        if OBJECTID_RE.match(oid) and url:
            urls.append(url)
    return sorted(set(urls))


def parse_property(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    m = re.search(r"Fusion\.globalContent=(\{.*?\});Fusion\.globalContentConfig", r.text, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))

    name = data.get("propertyName") or url.rstrip("/").split("/")[-1].replace("-", " ").title()
    address = data.get("fullAddress") or name
    lat = data.get("latitude")
    lng = data.get("longitude")
    sector = (data.get("sector") or ["Office"])[0]
    space_type = "Retail" if "retail" in sector.lower() else "Office"

    desc = re.sub(r"\s+", " ", (data.get("descriptionHTML") or "")).strip()[:1500]

    image_url = data.get("thumbnail") or ""
    if not image_url:
        images = data.get("images") or []
        if images:
            image_url = images[0].get("URL", "")

    # first ownership-side leasing contact (all contacts here are on
    # brookfieldproperties.com — this is the landlord's own leasing staff)
    c_role = c_name = c_email = c_phone = ""
    for c in data.get("contacts") or []:
        if c.get("emailAddress"):
            c_role = c.get("role", "")
            c_name = " ".join(p for p in [c.get("firstName"), c.get("lastName")] if p)
            c_email = c.get("emailAddress", "")
            c_phone = c.get("phoneNumber", "")
            break

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_row = {
        "landlord": "Brookfield Properties", "building_name": name, "address": address,
        "description": desc, "contact_role": c_role, "contact_name": c_name,
        "contact_email": c_email, "contact_phone": c_phone, "source_url": url,
        "scraped_at": scraped_at, "neighborhood": "",
        "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }

    rows = []
    for suite in data.get("vtsList") or []:
        if not suite.get("available", True):
            continue
        sqft = suite.get("area", {}).get("magnitude", "")
        floor = suite.get("floor", "")
        num = suite.get("suite", "")
        comp = suite.get("floorComposition", "")
        floor_suite = f"{comp.title()} floor {floor}".strip() if comp else f"Floor {floor}"
        if num:
            floor_suite += f", Suite {num}"
        rows.append({**base_row, "space_type": space_type,
                      "floor_suite": floor_suite, "size_sqft": sqft, "rent": "Upon request"})
    if not rows:
        rows.append({**base_row, "space_type": space_type,
                      "floor_suite": "", "size_sqft": "", "rent": ""})
    return rows


def main():
    urls = property_urls()
    print(f"{len(urls)} Brookfield NYC office/retail property pages")
    all_rows = []
    for url in urls:
        try:
            got = parse_property(url)
            all_rows.extend(got)
            if got:
                print(f"  {got[0]['building_name'][:40]:40s} "
                      f"{sum(1 for r in got if r['floor_suite'])} spaces")
        except Exception as e:
            print(f"  !! {url}: {type(e).__name__} {e}")
    with open("brookfield_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote brookfield_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
