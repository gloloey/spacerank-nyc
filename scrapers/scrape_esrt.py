"""
scrape_esrt.py — landlord #6: Empire State Realty Trust (esrtreit.com)
======================================================================
ARCHITECTURE (scouted 2026-07-20 via the browser):
  * WordPress. /leasing/ renders every published availability as an
    .availability-card inside per-property .property__availabilities
    sections. Each card:
      - .availability-card-main-info: "25,147 SF / Empire State Building /
        350 Fifth Avenue / Entire 30th Floor / New York, NY 10118"
      - .availability-card-more-info: Availability date, Condition
        (Whitebox / Prebuilt...), Floor Type — real suite-level facts we
        fold into the description for the semantic matcher.
      - a link to a /spaces/{slug} detail page (used as source_url).
  * The wp-json REST API for the "spaces" post type returns 401 — the
    rendered page is the public source of truth, so we parse that.
  * No public emails ("Book A Tour" form) -> contact recorded honestly as
    the space page link, like Rudin.
  * Street addresses are on every card -> geocoded via NYC GeoSearch.

ESRT is a public REIT (NYSE: ESRT) -> landlord_style "institutional".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.esrtreit.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def geocode(text, cache={}):
    if text in cache:
        return cache[text]
    out = (None, None)
    try:
        r = requests.get("https://geosearch.planninglabs.nyc/v2/search",
                         params={"size": 1, "text": f"{text}, New York, NY"},
                         timeout=10)
        feats = r.json().get("features", [])
        if feats:
            lng, lat = feats[0]["geometry"]["coordinates"]
            if 40.4 < lat < 41.1:
                out = (lat, lng)
        time.sleep(0.4)
    except Exception:
        pass
    cache[text] = out
    return out


def main():
    r = requests.get(BASE + "/leasing/", headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    for card in soup.select(".availability-card"):
        main_el = card.select_one(".availability-card-main-info")
        more_el = card.select_one(".availability-card-more-info")
        if not main_el:
            continue
        lines = [t.strip() for t in main_el.get_text("\n", strip=True).split("\n") if t.strip()]
        # expected: [SF, building, address, suite, city-state-zip] — be lenient
        sf = next((l for l in lines if re.match(r"^[\d,]+ ?SF$", l, re.I)), "")
        building = lines[1] if len(lines) > 1 else "?"
        address = next((l for l in lines if re.match(r"^\d+ ", l)), "")
        suite = next((l for l in lines if re.search(r"floor|suite|entire|partial|mezz", l, re.I)), "")
        city = next((l for l in lines if re.search(r"NY \d{5}|CT \d{5}", l)), "New York, NY")

        more = {}
        if more_el:
            mtxt = more_el.get_text("\n", strip=True).split("\n")
            for i in range(0, len(mtxt) - 1, 2):
                more[mtxt[i].strip().lower()] = mtxt[i + 1].strip()
        # WordPress lazy-loads images: src is a 1x1 data: placeholder and the
        # real file sits in data-src / data-lazy-src / srcset. Take the first
        # real URL and never accept a data: URI.
        image_url = ""
        for img_el in ([card.select_one("img")] +
                       list((card.find_parent(class_="property__availabilities") or card).select("img"))):
            if img_el is None:
                continue
            for attr in ("data-src", "data-lazy-src", "data-original", "src"):
                s = (img_el.get(attr) or "").split("?")[0].strip()
                if not s and attr == "src" and img_el.get("srcset"):
                    s = img_el["srcset"].split(",")[0].split(" ")[0]
                if s and not s.startswith("data:"):
                    image_url = s if s.startswith("http") else BASE + s
                    break
            if image_url:
                break

        link = card.select_one("a[href*='/spaces/']")
        url = link["href"].split("?")[0] if link else BASE + "/leasing/"

        # Westchester / Connecticut assets exist — keep NYC-metro rows only
        # if they geocode inside the city; others keep blank coords and are
        # honestly excluded by area filters.
        lat, lng = geocode(address or building) if address else (None, None)

        desc_bits = [f"{more.get('condition', '')} space".strip(),
                     more.get("floor type", ""),
                     f"available {more.get('availability', '')}".strip()]
        desc = ("; ".join(b for b in desc_bits if b and not b.startswith("available ;"))
                + f". At {building}, {address}." if building else "")

        rows.append({
            "landlord": "Empire State Realty Trust",
            "building_name": building,
            "address": address or building,
            "description": desc[:1200],
            "space_type": "Retail" if re.search(r"retail", str(card.get("class")) + suite, re.I) else "Office",
            "floor_suite": suite or "Space",
            "size_sqft": re.sub(r"[^\d]", "", sf),
            "rent": "Upon request",
            "contact_role": "Book a tour",
            "contact_name": "",
            "contact_email": "",
            "contact_phone": "",
            "source_url": url,
            "scraped_at": scraped_at,
            "neighborhood": "" if "NY" in city else city,
            "lat": lat if lat else "", "lng": lng if lng else "",
            "image_url": image_url,
        })
        print(f"  {building[:34]:34s} {suite[:26]:26s} {sf:>10s} geo={'Y' if lat else 'N'}")

    with open("data/raw/esrt_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote esrt_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
