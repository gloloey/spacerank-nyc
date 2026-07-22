"""
scrape_resnick.py — landlord #11: Jack Resnick & Sons (resnicknyc.com)
========================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * Two unpaginated, fully server-rendered pages list every current
    availability (WordPress + Elementor, no JS needed to see the data):
      https://resnicknyc.com/commercial/availabilities/  (office)
      https://resnicknyc.com/retail/availabilities/       (retail)
  * Each building is one `div.available-building` block: `h2` holds the
    building name, which doubles as its street address (e.g. "110 East
    59th Street") — same "name is the address" pattern as scrape_vornado.py.
    Inside, `table.desktop-only` has one `tr.retail-availability` row per
    space (cells: Floor/Suite | Size | Available date | Price | tour links
    | floorplan link), optionally followed by a `tr.remarks` row (a bullet
    list of building notes) that belongs to the PRECEDING availability row
    by DOM order, not any shared id — must pair them positionally.
  * One building on the office page (One Seaport Plaza / 199 Water St)
    has NO table at all — its real availability data lives on a separate
    external microsite (199water.nyc). Rather than guess at that
    building's address/coords from nothing, this scraper just skips any
    `available-building` block with no table (logged, not silently
    dropped) instead of inventing a placeholder row.
  * Coordinates: not embedded anywhere -> geocoded via NYC GeoSearch from
    the building-name-as-address string, same pattern as every other
    scraper here.
  * Photo: `div.image-container img` has a real, immediately-usable `src`
    (some `loading="lazy"` but always a real .jpg URL, never a `data:`
    placeholder — no lazy-load gotcha on this site).
  * Leasing contact: real named people on the owner's own domain, inside
    `div.contacts` — BUT the email is Cloudflare-obfuscated
    (`data-cfemail="<hex>"`, standard single-byte-XOR cipher: first byte
    is the key, XOR every remaining byte with it for the ASCII address).
    Confirmed by hand against a live sample before trusting it in code.
  * Rents: 100% "Upon Request" across every row checked -> stored as-is,
    never a guessed number.

Jack Resnick & Sons is family-owned and family-run since its founding
(the Resnick family; site literally titled "resnicknyc" and staffed by
named Resnick-era executives) -> landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://resnicknyc.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
PAGES = [(f"{BASE}/commercial/availabilities/", "Office"),
         (f"{BASE}/retail/availabilities/", "Retail")]

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
                         params={"size": 1, "text": f"{text}, New York, NY"}, timeout=10)
        feats = r.json().get("features", [])
        if feats:
            lng, lat = feats[0]["geometry"]["coordinates"]
            if 40.4 < lat < 41.1:
                out = (lat, lng)
    except Exception:
        pass
    cache[text] = out
    return out


def decode_cfemail(hexstr):
    """Cloudflare's email-obfuscation cipher: byte 0 is an XOR key applied
    to every remaining byte to recover the plain ASCII address."""
    try:
        raw = bytes.fromhex(hexstr)
        key = raw[0]
        return "".join(chr(b ^ key) for b in raw[1:])
    except Exception:
        return ""


def first_contact(block):
    c = block.select_one("div.contacts")
    if not c:
        return "", ""
    p = c.select_one("p")
    if not p:
        return "", ""
    spans = p.select("span")
    name = spans[0].get_text(strip=True) if spans else ""
    cf = p.select_one("[data-cfemail]")
    email = decode_cfemail(cf["data-cfemail"]) if cf else ""
    return name, email


def parse_building(block, kind, url):
    name_el = block.select_one("h2")
    name = re.sub(r"\s+", " ", name_el.get_text(" ", strip=True)) if name_el else "?"
    tbl = block.select_one("table")
    if not tbl:
        print(f"  skip (no availability table — external microsite): {name}")
        return []

    img = block.select_one("div.image-container img")
    image_url = (img.get("src") or "") if img else ""
    if image_url.startswith("data:"):
        image_url = ""

    c_name, c_email = first_contact(block)
    lat, lng = geocode(name)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    base_row = {
        "landlord": "Jack Resnick & Sons", "building_name": name, "address": name,
        "contact_role": "Leasing" if c_email else "", "contact_name": c_name,
        "contact_email": c_email, "contact_phone": "",
        "source_url": url, "scraped_at": scraped_at, "neighborhood": "",
        "lat": lat or "", "lng": lng or "", "image_url": image_url,
    }

    rows = []
    trs = tbl.select("tr")
    # a "remarks" row comes AFTER the availability row it describes (not
    # before) — attach it to the last-parsed row, don't stash it forward
    parsed = []
    for tr in trs:
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.select("td")]
        if "remarks" in (tr.get("class") or []):
            if parsed:
                parsed[-1]["remark"] = " ".join(c for c in cells if c.lower() != "remarks")
            continue
        if len(cells) < 2:
            continue
        floor_suite, size_txt = cells[0], cells[1]
        sqft = re.sub(r"[^\d]", "", size_txt)
        parsed.append({"floor_suite": floor_suite, "sqft": sqft, "remark": ""})

    for p in parsed:
        rows.append({**base_row, "space_type": kind,
                    "floor_suite": p["floor_suite"], "size_sqft": p["sqft"],
                    "rent": "Upon request",
                    "description": p["remark"]})
    return rows


def main():
    all_rows = []
    for url, kind in PAGES:
        soup = BeautifulSoup(get(url), "lxml")
        blocks = soup.select("div.available-building")
        print(f"{len(blocks)} {kind} buildings on {url}")
        for block in blocks:
            try:
                rows = parse_building(block, kind, url)
                all_rows.extend(rows)
                if rows:
                    print(f"  {rows[0]['building_name'][:40]:40s} {kind:7s} {len(rows)} spaces")
            except Exception as e:
                print(f"  !! block error: {type(e).__name__} {e}")

    with open("resnick_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote resnick_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
