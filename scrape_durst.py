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

# BUG FIX (found 2026-07-21 while adding a "search near a subway station"
# feature — a proximity search made an old bug visible: 6 buildings were
# all landing on the exact same wrong coordinates). The ORIGINAL regex
# below ran with re.search over the WHOLE page's flattened text, including
# the site nav (a menu literally listing every Durst property name back
# to back) and the footer (Durst's own HQ address, on every page). Because
# its middle group `[\w\.' ]{2,30}?` allows digits AND spaces, a non-greedy
# search could span clean across several property names in that nav menu —
# e.g. "1155 Avenue of the Americas 1133 Avenue" is two different real
# buildings' names fused into one bogus "address" by the regex matching
# from the first building's house number to the SECOND building's street
# suffix. That fake address then geocoded to a single wrong point, and
# every building whose real address happened to be unfindable in the page
# body fell into this same trap (they all matched the same nav-menu span).
#
# THE FIX: don't search the whole flattened page for an address-shaped
# string at all — use each Durst property page's own <meta name="keywords">
# tag, which (when page-specific, which is most of the time) lists the
# real street address as one clean, comma-isolated entry, e.g.
# "OWTC, One World Trade Center, ..., 285 Fulton Street, SOM, ...". Splitting
# on commas BEFORE pattern-matching makes cross-item contamination
# structurally impossible — each candidate is matched with re.fullmatch,
# so a match can only ever be that one isolated phrase, never a fusion of
# two. Falls back to the old flattened-text search (now scoped away from
# nav/footer) only if no keyword candidate looks like an address, and
# ultimately to the building's own name (unchanged behavior) if nothing
# useful is found anywhere — never fabricated.
ADDR_CANDIDATE_RE = re.compile(
    r"^\d{1,4}[\w\-]*(?:\s+(?:East|West|North|South))?\s+[A-Za-z0-9\.' ]{2,35}?"
    r"(?:Street|St\.?|Avenue|Ave\.?|Broadway|Place|Plaza|Square|Lane|Road)\.?$", re.I)
ADDR_FALLBACK_RE = re.compile(
    r"\d{1,4}[\w\-]* (?:East|West|North|South )?[A-Za-z\.' ]{2,30}?"
    r"(?:Street|St\.?|Avenue|Ave\.?|Broadway|Place|Plaza|Square|Lane|Road)", re.I)
# "42nd and Broadway" is a cross-streets DESCRIPTION, not a mailing address —
# it happens to satisfy the shape above (digit ... suffix word) but geocodes
# unreliably (a real bug found while fixing this: it landed a Times Square
# building in the Financial District). Reject anything joined by "and".
_CROSS_STREETS_RE = re.compile(r"\band\b", re.I)
_BOILERPLATE_SELECTORS = ["footer", "nav", "header", "#footerWrapper", "#footer",
                          "#header", "#mainNav", ".menu"]


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


def extract_address(soup):
    """See the BUG FIX note above ADDR_CANDIDATE_RE. Tries the page's own
    meta keywords first (page-specific, comma-isolated, safe from cross-
    item contamination); falls back to a body-text search with nav/footer/
    header removed; "" (caller falls back to the building name) if neither
    finds anything address-shaped."""
    kw = soup.select_one('meta[name="keywords"]')
    if kw and kw.get("content"):
        for item in kw["content"].split(","):
            item = item.strip()
            if ADDR_CANDIDATE_RE.match(item) and not _CROSS_STREETS_RE.search(item):
                return item
    for sel in _BOILERPLATE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    m = ADDR_FALLBACK_RE.search(soup.get_text(" ", strip=True))
    return m.group(0).strip() if m else ""


def property_meta(url):
    """Address + description + best-effort from a /properties page."""
    try:
        html = get(url)
    except Exception:
        return "", ""
    soup = BeautifulSoup(html, "lxml")
    addr = extract_address(soup)
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
        lat, lng = geocode(addr) if addr else (None, None)
        if lat is None and name != addr:
            lat, lng = geocode(name)       # extracted address didn't geocode — try the name
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
