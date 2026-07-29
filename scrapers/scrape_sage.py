"""
scrape_sage.py — landlord #12: Sage Realty Corporation (sagerealty.com)
=========================================================================
ARCHITECTURE (scouted 2026-07 via curl, verified by hand against live
fetches — not guessed):
  * The visible site is a fully React-hydrated SPA (server HTML is just
    <div id="app"></div>) — but it's fed by a clean, public, unauthenticated
    WordPress REST API on the same domain. No HTML parsing needed at all.
  * GET /wp-json/availabilities/all -> {"availabilities": [...], "properties":
    [...]}. Each availability: title, path, property: [{term_id, name, slug}],
    thumbnail.url, content: {suite, floor_name, floor_composition,
    availability_date, space_condition, remeasured_space_available (sqft),
    is_retail, rental_rate_max}.
  * GET /wp-json/property/all -> [{ID, title, path, ...}] — this "ID" is
    the WP POST id, a DIFFERENT id space than the availability's
    property[0].term_id (a taxonomy term id) and not always slug-matchable
    either (availability slug "2-gansevoort-street" vs property path slug
    "2-gansevoort" — confirmed by hand, so this scraper matches on the
    property NAME string, the one field guaranteed identical in both).
  * GET /wp-json/property/{POST_ID} -> info.location = {address, lat, lng}
    (exact, no geocoding needed) and info.contact = a list of contact
    GROUPS, e.g. "Property Contacts", "Sage Leasing Contacts",
    "JLL Office Leasing Contacts", "CBRE Retail Leasing Contacts",
    "Sage Member Experience Contacts". CONFIRMED BY HAND: several groups
    are third-party brokers (@jll.com, @cbre.com) mixed in on the same
    page as Sage's own staff — this scraper takes ONLY the "Sage Leasing
    Contacts" group (falling back to "Property Contacts") and filters to
    @sagerealty.com emails, per the project's owner-only-contacts rule.
  * Photo: thumbnail.url (availability) is a real, fully-qualified https
    URL delivered directly in JSON — no lazy-load, no data: URI.
  * Rents: `rental_rate_max` exists in the schema but was empty on every
    one of the 14 live availabilities checked -> "Upon request", honestly.

Sage Realty is a William Kaufman Organization affiliate — privately held,
not a public REIT, and "family-run" would overstate what's actually
publicly verifiable about its current ownership/governance structure, so
landlord_style is left unclassified (None) rather than guessed, same
reasoning as RXR Realty's entry above.
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests

BASE = "https://sagerealty.com/wp-json"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.8
OWN_DOMAIN = "@sagerealty.com"

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def get_json(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def leasing_contact(info):
    groups = {g.get("title", "").strip(): g for g in info.get("contact") or []}
    for title in ("Sage Leasing Contacts", "Property Contacts"):
        grp = groups.get(title)
        if not grp:
            continue
        for c in grp.get("contact_info") or []:
            email = ""
            phone = ""
            for link in c.get("links") or []:
                url = (link.get("link") or {}).get("url", "")
                if url.startswith("mailto:"):
                    email = url.replace("mailto:", "")
                elif url.startswith("tel:"):
                    phone = (link.get("link") or {}).get("title", "")
            if email.lower().endswith(OWN_DOMAIN):
                name = re.sub(r"<br\s*/?>", " ", c.get("name", "")).strip()
                return name, email, phone
    return "", "", ""


def main():
    avail_data = get_json(f"{BASE}/availabilities/all")
    prop_data = get_json(f"{BASE}/property/all")
    post_id_by_name = {p["title"]: p["ID"] for p in prop_data.get("properties", [])}

    detail_cache = {}
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for a in avail_data.get("availabilities", []):
        prop_ref = (a.get("property") or [{}])[0]
        name = prop_ref.get("name", "")
        post_id = post_id_by_name.get(name)
        if post_id is None:
            print(f"  !! no property post match for '{name}' — skipping")
            continue
        if post_id not in detail_cache:
            detail_cache[post_id] = get_json(f"{BASE}/property/{post_id}")
        info = detail_cache[post_id].get("info", {})
        loc = info.get("location", {})

        c_name, c_email, c_phone = leasing_contact(info)
        content = a.get("content", {})
        floor = content.get("floor_name", "")
        suite = content.get("suite", "")
        comp = content.get("floor_composition", "")
        floor_suite = f"{comp.title()} floor {floor}".strip() if comp else f"Floor {floor}"
        if suite:
            floor_suite += f", Suite {suite}"
        sqft = content.get("remeasured_space_available", "")
        rent = content.get("rental_rate_max", "")
        space_type = "Retail" if content.get("is_retail") else "Office"
        thumb = (a.get("thumbnail") or {}).get("url", "") or (content.get("thumbnail") or {}).get("url", "")

        rows.append({
            "landlord": "Sage Realty", "building_name": name,
            "address": loc.get("address", "") or name,
            "description": f"{content.get('space_condition', '')} space, "
                           f"{content.get('availability_type', '')} availability".strip(", "),
            "space_type": space_type, "floor_suite": floor_suite, "size_sqft": sqft,
            "rent": f"${rent}/SF" if rent else "Upon request",
            "contact_role": "Leasing" if c_email else "", "contact_name": c_name,
            "contact_email": c_email, "contact_phone": c_phone,
            "source_url": f"https://sagerealty.com{a.get('path', '')}",
            "scraped_at": scraped_at, "neighborhood": "",
            "lat": loc.get("lat", ""), "lng": loc.get("lng", ""),
            "image_url": thumb,
        })
        print(f"  {name[:34]:34s} {floor_suite[:26]:26s} {sqft} sf")

    with open("data/raw/sage_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote sage_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
