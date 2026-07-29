"""
scrape_rudin.py — SpaceRank NYC: Rudin Management scraper (landlord #2)
=======================================================================
Rudin's site is the OPPOSITE architecture of GFP's — a useful contrast:

  GFP:   availabilities hidden behind a JSON API, contacts in the HTML.
  Rudin: everything server-rendered in plain HTML (a Drupal site with a
         paginated /all-availabilities listing), but NO public leasing
         emails — inquiries go through a web form. Per our data rules the
         contact is therefore recorded honestly as a form link, never an
         invented email.

WHAT GETS SCRAPED:
  1. /all-availabilities?page=0,1,2...   (stop when a page has no cards)
       each card:  <article class="available-listing">
                     .card__pretitle   -> floor/suite
                     .card__title a    -> building name + link
                     .card__neighborhood, .card__sqft, .card__availability
  2. each building page:
       description  -> div.pattern--intro-content__description
       coordinates  -> embedded in the page; the building's own map pin is
                       the HIGHEST-PRECISION lat/lng in the source (the
                       6-decimal pair is Rudin's HQ footer map — excluded)

Output: rudin_listings.csv — same schema as gfp_listings.csv, plus
        neighborhood / lat / lng columns the cleaner knows how to use.

Run:  python scrape_rudin.py
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.rudin.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "SpaceRankNYC-student-project"}
HQ_LAT, HQ_LNG = "40.757756", "-73.972273"     # footer map, not a building pin

TYPE_BY_PATH = {"office-spaces": "Office", "retail-spaces": "Retail",
                "professional-suites": "Office"}


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def scrape_cards():
    """Walk the paginated availabilities list until a page comes back empty."""
    cards, page = [], 0
    while True:
        url = f"{BASE}/all-availabilities" + (f"?page={page}" if page else "")
        soup = BeautifulSoup(get(url), "lxml")
        found = soup.select("article.available-listing")
        if not found:
            break
        for a in found:
            title = a.select_one("h4.card__title a")
            cards.append({
                "suite": (a.select_one("h3.card__pretitle") or a).get_text(strip=True),
                "building": title.get_text(strip=True) if title else "",
                "href": title.get("href", "") if title else "",
                "hood": (a.select_one(".card__neighborhood") or a).get_text(strip=True),
                "sqft": (a.select_one(".card__sqft") or a).get_text(strip=True),
                "avail": (a.select_one(".card__availability") or a).get_text(strip=True),
            })
        page += 1
        time.sleep(2)
    # dedupe (the pager can repeat rows)
    seen, out = set(), []
    for c in cards:
        key = (c["building"], c["suite"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def scrape_building(href):
    """Description + the building's own (highest-precision) coordinates."""
    html = get(BASE + href)
    soup = BeautifulSoup(html, "lxml")
    desc = " ".join(dict.fromkeys(                       # dedupe, keep order
        p.get_text(" ", strip=True)
        for p in soup.select("div.pattern--intro-content__description p,"
                             "div.pattern--intro-content__description")))
    def best(pattern):
        vals = [v for v in re.findall(pattern, html) if v not in (HQ_LAT, HQ_LNG)]
        return max(vals, key=len) if vals else None     # longest = map pin
    lat, lng = best(r"\b40\.\d{6,}\b"), best(r"-7[34]\.\d{6,}\b")
    return {"description": desc,
            "lat": float(lat) if lat else "",
            "lng": float(lng) if lng else ""}


COLUMNS = ["landlord", "building_name", "address", "description", "space_type",
           "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
           "contact_email", "contact_phone", "source_url", "scraped_at",
           "neighborhood", "lat", "lng"]


def main():
    cards = scrape_cards()
    print(f"{len(cards)} availability cards found")

    buildings = {}
    for href in dict.fromkeys(c["href"] for c in cards):
        buildings[href] = scrape_building(href)
        print("  scraped", href)
        time.sleep(2)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for c in cards:
        b = buildings[c["href"]]
        path_type = c["href"].split("/")[2] if c["href"].count("/") >= 2 else ""
        rows.append({
            "landlord": "Rudin Management",
            "building_name": c["building"],
            "address": c["building"],
            "description": b["description"],
            "space_type": TYPE_BY_PATH.get(path_type, "Office"),
            "floor_suite": c["suite"],
            "size_sqft": int(re.sub(r"[^\d]", "", c["sqft"]) or 0) or "",
            "rent": "",                                   # Rudin doesn't publish rents
            "contact_role": "Leasing inquiry (web form)", # honest label — no public email
            "contact_name": "Rudin Leasing",
            "contact_email": "",
            "contact_phone": "",
            "source_url": BASE + c["href"],
            "scraped_at": now,
            "neighborhood": c["hood"],
            "lat": b["lat"], "lng": b["lng"],
        })

    with open("data/raw/rudin_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"saved rudin_listings.csv ({len(rows)} rows, "
          f"{len(buildings)} buildings)")


if __name__ == "__main__":
    main()
