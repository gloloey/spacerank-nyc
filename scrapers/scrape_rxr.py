"""
scrape_rxr.py — landlord #8: RXR Realty (rxr.com)
==================================================
ARCHITECTURE (scouted 2026-08 via curl, verified by hand against live
fetches — not guessed):
  * rxr.com itself is a dead end for per-space data: /portfolio is a
    server-rendered WordPress directory of building CARDS (name, borough
    tag, photo) with no square footage/floor/suite fields anywhere, and
    /availabilities/commercial/ is a marketing page whose "View
    Availabilities" button just anchor-links to a lead-gen contact form.
  * The real per-suite inventory lives on independently-built,
    per-building WordPress "microsites" that RXR runs on their own vanity
    domains (e.g. 1211aofa.com, 590madison.com). These are NOT uniform:
    different URL slugs, different table markup per building. Of the ~5
    such microsites discovered from the rxr.com/portfolio outbound links,
    only two were confirmed live with real availability tables at time of
    writing (one.clintonpark.com redirects to an unrelated residential
    site, redhooklogistics.com's /availabilities/ 404s, twoclintonpark.com
    is blocked by a third-party web filter) — so this scraper hardcodes a
    small, hand-verified registry of (building, base_url, table style)
    rather than crawling, exactly because a generic crawl would silently
    include broken/wrong-property pages. Extending the registry to more
    RXR microsites means verifying each one by hand first, same as this one.
  * Two table styles seen so far:
      "simple": <table class="table"> <tr><td>Unit</td><td>Type</td>
                <td>RSF</td><td>Availability</td><td>Condition</td>...
                (1211aofa.com/workspaces/)
      "floors": <table class="floors-table"> <tr data-floor="41">
                <td><a>...<div class="text-part">Partial 41</div>
                <div class="text-part">5,919 RSF</div>
                <div class="text-part">Pre-built</div></a></td></tr>
                (590madison.com/availabilities/)
  * No coordinates embedded on either microsite -> geocoded via NYC
    GeoSearch from the building's real street address (same pattern as
    scrape_esrt.py).
  * Real building photos: homepage hero/gallery <img srcset="..."> —
    full-resolution .jpg/.webp URLs directly in srcset, not lazy-loaded,
    never a data: URI.
  * Real ownership-side leasing contacts: each microsite's /contact/ page
    has named RXR employees with @rxr.com emails and direct-dial numbers
    (not brokers).
  * No rents published anywhere -> "Upon request", honestly.

RXR Realty is privately held (founded and led by Scott Rechler) — neither
a public REIT nor a multi-generation family firm in the Rudin/Durst/GFP
sense, so landlord_style is intentionally left unclassified (None) rather
than forced into either bucket.
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 1.0

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]

# hand-verified registry — see ARCHITECTURE above for why this isn't a crawl
BUILDINGS = [
    {
        "name": "1211 Avenue of the Americas",
        "address": "1211 Avenue of the Americas, New York, NY",
        "base": "https://1211aofa.com",
        "avail_path": "/workspaces/",
        "style": "simple",
    },
    {
        "name": "590 Madison Avenue",
        "address": "590 Madison Avenue, New York, NY",
        "base": "https://590madison.com",
        "avail_path": "/availabilities/",
        "style": "floors",
    },
]


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


def real_hero_image(base):
    """First non-lazy, non-logo, real-URL photo on the microsite's homepage."""
    soup = BeautifulSoup(get(base + "/"), "lxml")
    for im in soup.select("img"):
        for attr in ("srcset", "src", "data-src"):
            val = (im.get(attr) or "").split(",")[0].split(" ")[0].strip()
            if val and not val.startswith("data:") and "logo" not in val.lower():
                return val if val.startswith("http") else base + val
    return ""


def ownership_contacts(base):
    """Real named RXR staff with @rxr.com emails from the microsite's /contact/ page."""
    out = []
    try:
        soup = BeautifulSoup(get(base + "/contact/"), "lxml")
    except Exception:
        return out
    for a in soup.select("a[href^='mailto:']"):
        email = a["href"].replace("mailto:", "").split("?")[0].strip()
        if not email.lower().endswith("@rxr.com"):
            continue
        block = a.find_parent(["div", "li", "article", "section"])
        name = ""
        if block:
            txt = re.sub(r"\s+", " ", block.get_text(" | ", strip=True))
            name = txt.split("|")[0].strip()
        out.append((name, email))
    return out


def parse_simple_table(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    tbl = soup.select_one("table.table")
    if not tbl:
        return rows
    for tr in tbl.select("tr"):
        tds = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.select("td")]
        if len(tds) < 3:
            continue
        unit, kind, rsf = tds[0], tds[1], tds[2]
        condition = tds[4] if len(tds) > 4 else ""
        sqft = re.sub(r"[^\d]", "", rsf)
        if not sqft:
            continue
        rows.append({
            "space_type": "Retail" if "retail" in kind.lower() else "Office",
            "floor_suite": f"Floor {unit}" + (f" ({condition})" if condition else ""),
            "size_sqft": sqft,
        })
    return rows


def parse_floors_table(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    tbl = soup.select_one("table.floors-table")
    if not tbl:
        return rows
    for tr in tbl.select("tr[data-floor]"):
        floor = tr.get("data-floor", "")
        parts = [re.sub(r"\s+", " ", d.get_text(" ", strip=True))
                 for d in tr.select("div.text-part")]
        if len(parts) < 2:
            continue
        comp = re.sub(r"\d+\s*$", "", parts[0]).strip() or "Partial"
        sqft = re.sub(r"[^\d]", "", parts[1])
        condition = parts[2] if len(parts) > 2 else ""
        if not sqft:
            continue
        rows.append({
            "space_type": "Office",
            "floor_suite": f"{comp} floor {floor}" + (f" ({condition})" if condition else ""),
            "size_sqft": sqft,
        })
    return rows


def parse_building(b):
    url = b["base"] + b["avail_path"]
    html = get(url)
    parser = parse_simple_table if b["style"] == "simple" else parse_floors_table
    avail_rows = parser(html)

    lat, lng = geocode(b["address"])
    image_url = real_hero_image(b["base"])
    contacts = ownership_contacts(b["base"])
    c_name, c_email = contacts[0] if contacts else ("", "")

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_row = {
        "landlord": "RXR Realty", "building_name": b["name"], "address": b["address"],
        "description": f"Office space at {b['name']}, New York.",
        "rent": "Upon request",
        "contact_role": "Leasing" if c_email else "", "contact_name": c_name,
        "contact_email": c_email, "contact_phone": "",
        "source_url": url, "scraped_at": scraped_at, "neighborhood": "",
        "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }
    rows = [{**base_row, **r} for r in avail_rows]
    if not rows:
        rows.append({**base_row, "space_type": "Office", "floor_suite": "", "size_sqft": "", "rent": ""})
    return rows


def main():
    print(f"{len(BUILDINGS)} hand-verified RXR NYC microsites")
    all_rows = []
    for b in BUILDINGS:
        try:
            got = parse_building(b)
            all_rows.extend(got)
            print(f"  {b['name'][:40]:40s} {sum(1 for r in got if r['floor_suite'])} spaces")
        except Exception as e:
            print(f"  !! {b['name']}: {type(e).__name__} {e}")
    with open("data/raw/rxr_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote rxr_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
