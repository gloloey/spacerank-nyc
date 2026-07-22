"""
scrape_timeequities.py — landlord #15: Time Equities Inc (timeequities.com)
=============================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * Server-rendered HTML (Craft CMS), no JS needed to see availability data.
  * Listing page: https://timeequities.com/properties?avail=retail — cards
    are `div.property-block-basic.image-block.prop-result`, each wrapping
    an `<a href="/availabilities/<slug>">`. (TEI's "NYC Office" nav filter,
    avail=retail-sales, returned 0 results at scrape time — not an error,
    just nothing currently published in that category; this scraper only
    covers the retail feed, which IS populated.)
  * Each detail page (https://timeequities.com/availabilities/<slug>) has
    a `<h3>` with the full address as one unstructured string (e.g. "1152
    First Ave New York New York 10065 United States" — regex-split into
    street/zip, falling back to the raw string if the pattern doesn't
    match, never guessed at) and `div.meta-group` blocks: `span.meta-label`
    (field name) + `h4` (value). Fields seen: Square Footage, Unit,
    Price Per RSF, Price Per Month, Total Frontage, Cross Streets, Date
    Available, Subways.
  * IMPORTANT — caught by hand before trusting it: "Price Per RSF" (a true
    $/SF/year figure, e.g. $131, $150, $90 — normal NYC retail range) and
    "Price Per Month" (a raw monthly dollar amount for the whole space,
    e.g. $25,000, $31,125) are DIFFERENT units. This project's `rent`
    field is always $/SF/year elsewhere in the dataset, and
    clean_dataset.py's parse_rent_psf() extracts ANY "$number" from the
    string with no unit check — so storing a monthly figure there would
    silently corrupt every budget comparison downstream. Only "Price Per
    RSF" is ever written to `rent`; "Price Per Month" is never touched.
  * Coordinates: not embedded -> geocoded via NYC GeoSearch from the
    parsed street address, same pattern as every other scraper here.
  * Photo: `img.js-lazy` has a real (if low-res "_loader" placeholder) `src`
    immediately, never a `data:` URI; the higher-res "_medium" variant is
    pulled from `data-srcset` instead when present.
  * Ownership-side leasing CONTACT: no per-listing contact exists on detail
    pages — the site's own /contact page lists real named/departmental
    staff on the timeequities.com domain (e.g. "National Retail",
    retail@timeequities.com). Used as the contact for every retail row,
    same "real team, not per-suite person" honesty as scrape_feil.py.

Time Equities Inc is privately held; this scraper does not have a citable
public source pinning down "family-run" in the same verifiable sense as
Rudin/Durst/GFP/Silverstein/Resnick/Feil/Two Trees, so landlord_style is
left unclassified rather than assumed, same reasoning as RXR Realty.
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://timeequities.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url, params=None):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
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


def national_retail_contact():
    soup = BeautifulSoup(get(f"{BASE}/contact"), "lxml")
    for a in soup.select("a[href^='mailto:']"):
        email = a["href"].replace("mailto:", "").split("?")[0].strip()
        if email == "retail@timeequities.com":
            block = a.find_parent(["div", "li", "p"])
            phone_a = block.select_one("a[href^='tel:']") if block else None
            phone = phone_a.get_text(strip=True) if phone_a else ""
            return "National Retail", email, phone
    return "", "", ""


def parse_address(h3_text):
    m = re.match(r"^(.*?)\s+New York\s+New York\s+(\d{5})", h3_text)
    if m:
        return f"{m.group(1).strip()}, New York, NY {m.group(2)}"
    return h3_text  # honest fallback — never guessed/fabricated


def parse_detail(url, c_name, c_email, c_phone):
    soup = BeautifulSoup(get(url), "lxml")
    h3 = soup.select_one("h3")
    address = parse_address(re.sub(r"\s+", " ", h3.get_text(" ", strip=True))) if h3 else ""

    fields = {}
    for mg in soup.select("div.meta-group"):
        label_el, val_el = mg.select_one("span.meta-label"), mg.select_one("h4")
        if label_el and val_el:
            fields[label_el.get_text(strip=True)] = val_el.get_text(" ", strip=True)

    sqft = re.sub(r"[^\d]", "", fields.get("Square Footage", ""))
    psf = fields.get("Price Per RSF", "")
    rent = f"{psf}/SF" if psf.strip().startswith("$") else "Upon request"  # never the monthly figure — see docstring

    unit = fields.get("Unit", "")
    cross = fields.get("Cross Streets", "")
    desc_bits = [unit, f"near {cross}" if cross else "", fields.get("Total Frontage", "")]
    description = "; ".join(b for b in desc_bits if b)

    img = soup.select_one("img.js-lazy")
    image_url = ""
    if img:
        srcset = img.get("data-srcset", "")
        if srcset:
            image_url = srcset.split(",")[0].strip().split(" ")[0]
        if not image_url or image_url.startswith("data:"):
            image_url = img.get("src", "")
    if image_url.startswith("data:"):
        image_url = ""

    lat, lng = geocode(address) if address else (None, None)

    return {
        "landlord": "Time Equities", "building_name": address.split(",")[0] if address else "",
        "address": address, "description": description[:1200], "space_type": "Retail",
        "floor_suite": unit or "Space", "size_sqft": sqft, "rent": rent,
        "contact_role": "Leasing" if c_email else "", "contact_name": c_name,
        "contact_email": c_email, "contact_phone": c_phone,
        "source_url": url, "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "neighborhood": "", "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }


def main():
    c_name, c_email, c_phone = national_retail_contact()
    soup = BeautifulSoup(get(f"{BASE}/properties", params={"avail": "retail"}), "lxml")
    cards = soup.select("div.property-block-basic.image-block.prop-result")
    print(f"{len(cards)} Time Equities NYC retail availabilities")

    rows = []
    for card in cards:
        a = card.select_one("a[href^='/availabilities/']")
        if not a:
            continue
        url = BASE + a["href"]
        try:
            row = parse_detail(url, c_name, c_email, c_phone)
            rows.append(row)
            print(f"  {row['building_name'][:34]:34s} {row['floor_suite'][:20]:20s} {row['size_sqft']} sf")
        except Exception as e:
            print(f"  !! {url}: {type(e).__name__} {e}")

    with open("timeequities_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote timeequities_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
