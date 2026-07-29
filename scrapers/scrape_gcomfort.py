"""
scrape_gcomfort.py — landlord #16: George Comfort & Sons (gcomfort.com)
=========================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * ONE server-rendered page lists every current availability across the
    whole portfolio: https://www.gcomfort.com/availabilities/ — no JS
    needed, no hidden API. BUT its raw HTML is malformed (a stray
    `</strong>` deep inside a nested table, confirmed by hand) which makes
    BeautifulSoup+lxml silently mis-parse the tree (it "sees" only ~40
    elements in a 460KB document instead of hundreds) — caught by
    spot-checking the parsed output count against the raw file size
    BEFORE trusting a selector-based scrape. This scraper regexes the raw
    HTML text directly instead of relying on BeautifulSoup's tree.
  * ALSO caught by hand: the entire availabilities section appears TWICE
    in the raw HTML (a full duplicate — desktop/mobile layout variants of
    the identical content, not two different data sets). Scraping both
    copies would double-count every listing. This scraper only reads the
    first occurrence of the last known building ("2 Wall Street") and
    stops there, discarding the duplicate second half.
  * Structure per building: an `<a href="https://www.gcomfort.com/
    portfolio-items/<slug>/"><span class="btn-space ...">FULL ADDRESS
    IN CAPS</span></a>` header, followed by one or more
    `<table class="avail-table">` blocks, each with rows tagged
    `<strong>FLOOR</strong><br><VALUE>`, `<strong>RSF</strong><br>
    <VALUE>`, `<strong>TYPE</strong><br>OFFICE|RETAIL`, `<strong>
    OCCUPANCY</strong><br><VALUE>`. A handful of buildings (e.g. "The
    Centre at Purchase") repeat their own header multiple times with
    separate table blocks in between — this scraper merges all rows
    found under every occurrence of a given building's header/URL rather
    than assuming one header = one block.
  * ~17 buildings total portfolio-wide; only those whose header contains
    ", NEW YORK, NY" are kept (the rest are Westchester/Stamford CT/
    Hamilton NJ and are out of this project's NYC scope).
  * Coordinates: not on the availabilities page, but each building's own
    `/portfolio-items/<slug>/` page embeds a Google Maps iframe URL with
    `!2d<lng>!3d<lat>` — regexed out directly, no NYC GeoSearch needed.
  * Photo: a real, immediately-usable wp-content/uploads image URL on the
    portfolio-items page — no lazy-load placeholder, no data: URI.
  * Ownership-side leasing CONTACT: real named staff in `div class=
    "contact-info"` blocks on the portfolio-items page (e.g. Alexander N.
    Bermingham, Head of NYC Leasing, abermingham@gcomfort.com) — genuinely
    on the owner's own domain, not a broker.
  * Rents: never published anywhere on either page type -> "Upon
    request", honestly.

George Comfort & Sons is a privately-held, multi-generation family-run
NYC real estate firm (founded by George Comfort, still family-led) ->
landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests

BASE = "https://www.gcomfort.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8

BUILDING_RE = re.compile(
    r'href="(https://www\.gcomfort\.com/portfolio-items/[^"]+)"><span class="btn-space[^"]*">([^<]+)</span>')
ROW_RE = re.compile(
    r'<strong>FLOOR</strong><br>([^<]*)</td>.*?'
    r'<strong>RSF</strong><br>([^<]*)</td>.*?'
    r'<strong>TYPE</strong><br>([^<]*)</td>.*?'
    r'<strong>OCCUPANCY</strong><br>([^<]*)', re.S)
LATLNG_RE = re.compile(r"!2d(-?[\d.]+)!3d(-?[\d.]+)")
IMG_RE = re.compile(r'<img[^>]+src="(https://www\.gcomfort\.com/wp-content/uploads/[^"]+\.(?:jpg|jpeg|png|webp))"')
CONTACT_RE = re.compile(r'class="contact-info"[^>]*>\s*<p><strong>([^<]+)</strong><br>\s*'
                        r'([^<]*)<br>\s*([^<]*)<br>\s*([\w.+-]+@gcomfort\.com)', re.S)

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def nyc_buildings(raw):
    """De-duplicate the whole-page-repeated availabilities section (see
    docstring): the ENTIRE building sequence repeats verbatim once (a
    desktop/mobile layout duplicate), but several individual buildings
    ALSO legitimately repeat their own header more than once within a
    single copy (e.g. "The Centre at Purchase") — so "stop at the first
    repeated key" breaks too early. The real boundary is the SECOND
    occurrence of the very FIRST building's key, since only a full-page
    duplicate reproduces building #1 again from scratch."""
    all_matches = list(BUILDING_RE.finditer(raw))
    first_key = all_matches[0].group(1)
    second_start = next((m.start() for m in all_matches[1:] if m.group(1) == first_key), len(raw))
    matches = [m for m in all_matches if m.start() < second_start]

    buildings = {}
    for i, m in enumerate(matches):
        url, addr = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if ", NEW YORK, NY" not in addr.upper():
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else second_start
        window = raw[start:end]
        rows = ROW_RE.findall(window)
        key = (url, addr)
        buildings.setdefault(key, []).extend(rows)
    return buildings


def building_detail(url):
    html = get(url)
    m = LATLNG_RE.search(html)
    lat, lng = (m.group(2), m.group(1)) if m else ("", "")
    img = IMG_RE.search(html)
    image_url = img.group(1) if img else ""
    contacts = CONTACT_RE.findall(html)
    c_name, c_email, c_phone = "", "", ""
    if contacts:
        name, _title, phone, email = contacts[0]
        c_name, c_email, c_phone = name.strip(), email.strip(), phone.strip()
    return lat, lng, image_url, c_name, c_email, c_phone


def main():
    raw = get(f"{BASE}/availabilities/")
    buildings = nyc_buildings(raw)
    print(f"{len(buildings)} George Comfort & Sons NYC buildings")

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for (url, addr), avail_rows in buildings.items():
        def _titlecase(s):
            s = s.title()
            s = re.sub(r"\bNy\b", "NY", s)
            return re.sub(r"(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), s)
        name = _titlecase(addr.split(",")[0])
        addr = _titlecase(addr)
        try:
            lat, lng, image_url, c_name, c_email, c_phone = building_detail(url)
        except Exception as e:
            print(f"  !! {name}: {type(e).__name__} {e}")
            lat = lng = image_url = c_name = c_email = c_phone = ""

        base_row = {
            "landlord": "George Comfort & Sons", "building_name": name,
            "address": addr, "contact_role": "Leasing" if c_email else "",
            "contact_name": c_name, "contact_email": c_email, "contact_phone": c_phone,
            "source_url": url, "scraped_at": scraped_at, "neighborhood": "",
            "lat": lat, "lng": lng, "image_url": image_url,
        }
        n = 0
        for floor, rsf, kind, occ in avail_rows:
            sqft = re.sub(r"[^\d]", "", rsf)
            if not sqft:
                continue
            space_type = "Retail" if "retail" in kind.lower() else "Office"
            rows.append({**base_row, "space_type": space_type,
                        "floor_suite": floor.strip(), "size_sqft": sqft,
                        "rent": "Upon request",
                        "description": f"{occ.strip()} availability".strip()})
            n += 1
        print(f"  {name[:34]:34s} {n} spaces")

    with open("data/raw/gcomfort_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote gcomfort_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
