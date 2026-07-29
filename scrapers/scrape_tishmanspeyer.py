"""
scrape_tishmanspeyer.py — landlord #18: Tishman Speyer (tishmanspeyer.com)
============================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * A genuinely different tech stack from every scraper already in this
    repo: Next.js (server-rendered) backed by Sanity CMS, not WordPress and
    not a hidden REST/AJAX API. Every page embeds its full page-data as one
    JSON blob in `<script id="__NEXT_DATA__" type="application/json">` —
    no JS execution needed, just parse that one script tag.
  * City listing page (https://www.tishmanspeyer.com/properties/new-york)
    -> data["props"]["pageProps"]["propertiesListing"]: one entry per NYC
    property with title, a "types" tag list (Office towers are tagged
    "Mixed-Use" + "Retail" in Tishman's own taxonomy — e.g. Rockefeller
    Center, 300 Park, The Spiral are all "Mixed-Use,Retail" — while
    straight apartment buildings are tagged only "Residential" or
    "Residential,Retail"), a geopoint (exact lat/lng, no geocoding
    needed), and cta.url linking to the per-building detail page.
  * IMPORTANT SCOPE DECISION: this project is a commercial tenant-matching
    engine, not a residential search tool, so only properties carrying
    Tishman's own "Mixed-Use" tag are kept (their label for combined
    office+retail commercial towers); pure "Residential"/"Residential,
    Retail"-tagged buildings are skipped outright — never scraped, never
    force-fit into a space_type they aren't.
  * Detail page -> data["props"]["pageProps"]["property"]: exact geopoint,
    a real named leasing contact under contact.leasingContacts[0]
    (firstName, lastName, role, email, phone — genuinely the owner's own
    leasing staff, e.g. "Samantha Augarten, Managing Director, Leasing,
    SAugarte@TishmanSpeyer.com"), heroImage.src (a real, immediately-
    usable cdn.sanity.io image URL, no placeholder), and overviewText.body
    / propertyNeighborhood.body prose describing the building.
  * IMPORTANT LIMITATION (same pattern as scrape_brause.py, scrape_
    vornado.py, scrape_silverstein.py): no per-suite/floor availability
    table exists anywhere on this marketing-oriented site. Some detail
    pages link an "See Availabilities"-equivalent CTA off-domain to a
    third-party leasing marketplace (externalWebsite/externalCTA fields,
    seen used by other Tishman-adjacent sites) — this scraper never
    follows those; per CLAUDE.md rule 3 (owner sites only, never
    brokers/marketplaces), only the owner's own domain is read. One row
    per building, size_sqft left blank — honest, not invented.
  * No email/phone missing here (unlike Brause/Rudin) — Tishman Speyer
    publishes real per-building leasing-contact emails directly in the
    page data, so contact_email is populated for every row.

Tishman Speyer was co-founded by Robert Tishman and Jerry Speyer; its
current Chairman & CEO, Rob Speyer, is Jerry Speyer's son — multi-
generation family leadership, same citable pattern as Rudin/Durst ->
landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import json
import re
import time
from datetime import datetime, timezone

import requests

BASE = "https://www.tishmanspeyer.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
# "Greenwich American Center" is filed under Tishman's /properties/new-york
# city page but its own geopoint (41.098, -73.724) is Greenwich, CT — out
# of NYC-metro scope, same exclusion pattern used in scrape_brause.py for
# Congress Park Centre (Saratoga Springs).
EXCLUDE_TITLES = {"Greenwich American Center"}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else {}


def commercial_listings():
    data = next_data(get(f"{BASE}/properties/new-york"))
    listing = data.get("props", {}).get("pageProps", {}).get("propertiesListing", [])
    out = []
    for p in listing:
        types = {t.get("name", "").strip() for t in (p.get("types") or [])}
        cta = p.get("cta") or {}
        url = cta.get("url") or ""
        # only Tishman's own "Mixed-Use" (office+retail) tag, never
        # straight-residential buildings; see docstring for reasoning.
        title = p.get("title", "").strip()
        if "Mixed-Use" not in types or not url or title in EXCLUDE_TITLES:
            continue
        out.append({"title": title, "url": BASE + url})
    return out


def parse_property(title, url):
    data = next_data(get(url))
    prop = data.get("props", {}).get("pageProps", {}).get("property", {})

    geopoint = prop.get("geopoint") or {}
    lat, lng = geopoint.get("lat", ""), geopoint.get("lng", "")

    hero = prop.get("heroImage") or {}
    image_url = hero.get("src", "")
    if image_url.startswith("data:"):
        image_url = ""

    overview = (prop.get("overviewText") or {}).get("body", "")
    neighborhood_text = (prop.get("propertyNeighborhood") or {}).get("body", "")
    description = re.sub(r"\s+", " ", (overview or neighborhood_text)).strip()[:1200]

    contacts = (prop.get("contact") or {}).get("leasingContacts") or []
    c_name = c_email = c_phone = c_role = ""
    for c in contacts:
        if c.get("isContactOnly"):
            continue
        name = " ".join(p for p in [c.get("firstName"), c.get("lastName")] if p)
        if name:
            c_name, c_email, c_phone, c_role = name, c.get("email", ""), c.get("phone", ""), c.get("role", "Leasing")
            break

    return {
        "landlord": "Tishman Speyer", "building_name": title,
        "address": f"{title}, New York, NY", "description": description,
        "space_type": "Office", "floor_suite": "Available — contact for space details",
        "size_sqft": "", "rent": "Upon request",
        "contact_role": "Leasing" if c_name else "", "contact_name": c_name,
        "contact_email": c_email, "contact_phone": c_phone,
        "source_url": url, "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "neighborhood": "", "lat": lat, "lng": lng, "image_url": image_url,
    }


def main():
    listings = commercial_listings()
    print(f"{len(listings)} Tishman Speyer NYC commercial (office+retail) buildings")
    rows = []
    for item in listings:
        try:
            row = parse_property(item["title"], item["url"])
            rows.append(row)
            print(f"  {row['building_name'][:34]:34s} contact={row['contact_name'] or '(none)'}")
        except Exception as e:
            print(f"  !! {item['title']}: {type(e).__name__} {e}")

    with open("tishmanspeyer_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote tishmanspeyer_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
