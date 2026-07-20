"""
scrape_durst.py — landlord #5: The Durst Organization (durst.org)
=================================================================
ARCHITECTURE (scouted 2026-07-20 via the browser):
  * ONE page — /availabilities — lists every current availability,
    grouped by building in .property-availabilities blocks. Each block:
      - building name + link to its /properties/{slug} page
      - a table: Floorplan | Space | Sq. Ft. | Rental | Possession | Type
                 | COMMENTS | Assets
        COMMENTS is a real PER-SUITE description (rare and precious —
        it feeds the semantic matcher with suite-level text).
  * Types include Office / Retail / "Durst Ready Office" (prebuilt program)
    / Broadcast / Residential. We keep commercial types only and map
    "Durst Ready Office" -> Office (noting the program in the description).
  * Rents: mostly "Upon Request", occasionally numeric — parsed downstream.
  * No public emails -> contact recorded honestly as the building's
    /properties page (inquiry form), like Rudin.
  * Coordinates: the /properties/{slug} page contains the street address;
    we geocode it with the free NYC GeoSearch API. The "Durst Ready"
    block is a cross-building program page, not a building -> skipped.

Durst is family-owned since 1915 -> landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.durst.org"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
KEEP_TYPES = {"office", "retail", "durst ready office", "broadcast"}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]

ADDR_RE = re.compile(
    r"\d{1,4}[\w\-]* (?:East|West|North|South )?[\w\.' ]{2,30}?"
    r"(?:Street|St\.?|Avenue|Ave\.?|Broadway|Place|Plaza|Lane|Road)", re.I)


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def geocode(text):
    """Free NYC city geocoder — no key. Returns (lat, lng) or (None, None)."""
    try:
        r = requests.get("https://geosearch.planninglabs.nyc/v2/search",
                         params={"size": 1, "text": f"{text}, New York, NY"},
                         timeout=10)
        feats = r.json().get("features", [])
        if feats:
            lng, lat = feats[0]["geometry"]["coordinates"]
            return lat, lng
    except Exception:
        pass
    return None, None


def property_meta(url):
    """Address + description + best-effort from a /properties page."""
    try:
        html = get(url)
    except Exception:
        return "", ""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = ADDR_RE.search(text)
    addr = m.group(0).strip() if m else ""
    desc = ""
    for p in soup.select("p"):
        t = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(t) > 80:
            desc = t[:1200]
            break
    return addr, desc


def main():
    soup = BeautifulSoup(get(BASE + "/availabilities"), "lxml")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_rows = []
    for block in soup.select(".property-availabilities"):
        name_el = block.select_one("h2, h3, a[href*='/properties/']")
        link_el = block.select_one("a[href*='/properties/']")
        name = re.sub(r"\s+", " ", name_el.get_text(" ", strip=True)).strip() if name_el else "?"
        if name.lower() == "durst ready":
            continue                       # a program page, not a building
        url = link_el["href"] if link_el else BASE + "/availabilities"
        if url.startswith("/"):
            url = BASE + url
        addr, desc = property_meta(url)
        img_el = block.select_one("img[src]")
        image_url = ""
        if img_el:
            s = img_el["src"]
            image_url = s if s.startswith("http") else BASE + s
        # the building name is often itself the address (825 Third Avenue)
        geocode_text = addr or name
        lat, lng = geocode(geocode_text)
        if lat and not (40.4 < lat < 41.1):
            lat = lng = None               # e.g. Philadelphia — drop coords
        n_spaces = 0
        for tr in block.select("tbody tr"):
            tds = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.select("td")]
            if len(tds) < 6:
                continue
            _plan, space, sqft, rental, possession, stype = tds[:6]
            comments = tds[6] if len(tds) > 6 else ""
            if stype.strip().lower() not in KEEP_TYPES:
                continue                   # residential etc.
            sq = re.sub(r"[^\d]", "", sqft.split("-")[-1])   # "1,255 - 16,956" -> max
            mapped = "Retail" if "retail" in stype.lower() else "Office"
            note = " (Durst Ready prebuilt)" if "durst ready" in stype.lower() else ""
            all_rows.append({
                "landlord": "The Durst Organization",
                "building_name": name,
                "address": addr or name,
                "description": (comments + note + ". " + desc).strip(". ")[:1500],
                "space_type": mapped,
                "floor_suite": space or "Space",
                "size_sqft": sq or "",
                "rent": rental or "Upon request",
                "contact_role": "Inquiry form",
                "contact_name": "",
                "contact_email": "",
                "contact_phone": "",
                "source_url": url,
                "scraped_at": scraped_at,
                "neighborhood": "",
                "lat": lat if lat else "", "lng": lng if lng else "",
                "image_url": image_url,
            })
            n_spaces += 1
        print(f"  {name[:40]:40s} {n_spaces} spaces  geo={'Y' if lat else 'N'}")
    with open("durst_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote durst_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
