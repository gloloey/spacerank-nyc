"""
scrape_silverstein.py — landlord #9: Silverstein Properties (silversteinproperties.com)
========================================================================================
ARCHITECTURE (scouted 2026-08 via curl, field names verified by hand
against a live API call — not guessed):
  * The marketing site is Next.js, but it's backed by a PUBLIC, unauthenticated
    Directus CMS instance: GET
    https://silverstein-properties.directus.app/items/properties?limit=-1
    returns every property as flat JSON in one request — no HTML parsing,
    no auth, no rate-limit headers observed (be conservative anyway: one
    request total).
  * Fields used: title, address, sector ("Office"/"Retail"/"Residential"/
    "Mixed-use"/"Hotels"), location ("New York city" for NYC), type
    ("Current property" vs "In development" — we keep only current), plus
    mapbox_latitude/mapbox_longitude (real embedded coordinates, present on
    most current NYC office buildings), hero_image (a Directus file UUID —
    the real photo URL is https://<directus-host>/assets/<uuid>, verified
    to return a real image/jpeg with no auth), contact (an array mixing
    Silverstein's own staff with co-broker contacts at CBRE/Cushman &
    Wakefield on some buildings — filtered to @silvprop.com only, per the
    project's ownership-side-contacts-only rule; buildings where Silverstein
    itself has no listed contact are left with an honestly blank contact,
    same convention as scrape_rudin.py).
  * IMPORTANT LIMITATION: unlike GFP/Rudin/SLG/Vornado/Durst/ESRT, this API
    exposes BUILDING-level data only — one "availability" status
    (Available/Coming soon) and a free-text "floor_plates" blurb (e.g.
    "10,800 - 26,900 rsf" or a multi-floor breakdown), never a discrete
    per-suite table the way VTS-integrated sites publish. Rather than
    invent fake per-suite rows out of that prose, this scraper emits one
    row per building (matching the existing "portfolio row with no
    availabilities" pattern in scrape_vornado.py) and folds the floor-plate
    text into the description; size_sqft is left blank (neutral 0.5 in the
    matching engine) unless the field is a single unambiguous number.
  * No rents published anywhere -> "Upon request", honestly.

Silverstein Properties was founded in 1957 by Larry Silverstein and remains
led by the Silverstein family (Larry Silverstein and his daughter Lisa
Silverstein, President) -> landlord_style "family-run".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests

API = "https://silverstein-properties.directus.app/items/properties?limit=-1"
ASSET_BASE = "https://silverstein-properties.directus.app/assets/"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
OWN_DOMAIN = "@silvprop.com"

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng", "image_url"]


def single_number(text):
    """Only accept floor_plates as a real size if it's ONE unambiguous number
    (no range, no multi-floor breakdown) — never guess/average a range."""
    if not text:
        return ""
    nums = re.findall(r"[\d,]+(?=\s*rsf)", text, re.I)
    if len(nums) == 1:
        return re.sub(r"[^\d]", "", nums[0])
    return ""


def main():
    r = requests.get(API, headers=HEADERS, timeout=30)
    r.raise_for_status()
    items = r.json().get("data", [])
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    for it in items:
        if it.get("location") != "New York city":
            continue
        if it.get("type") != "Current property":
            continue
        if it.get("sector") != "Office":
            continue

        name = (it.get("title") or "").strip()
        address = (it.get("address") or name).strip()
        lat = it.get("mapbox_latitude") or ""
        lng = it.get("mapbox_longitude") or ""
        hero = it.get("hero_image")
        image_url = ASSET_BASE + hero if hero else ""
        plates = (it.get("floor_plates") or "").strip()

        c_name = c_email = ""
        for c in it.get("contact") or []:
            email = (c.get("email") or "")
            if email.lower().endswith(OWN_DOMAIN):
                c_name = c.get("name", "")
                c_email = email
                break

        desc = f"{name}, {address}."
        if plates:
            desc += f" Floor plates: {plates}."

        is_avail = it.get("availability") == "Available"
        # No per-suite floor/unit string exists in this API (see LIMITATION
        # above) — floor_suite must still be a non-empty, honest descriptor
        # of what's actually known, since clean_dataset.py's availability
        # flag keys off floor_suite being populated.
        floor_suite = (re.sub(r"\s+", " ", plates).strip() if is_avail and plates
                       else "Available — contact for floor plans" if is_avail else "")

        rows.append({
            "landlord": "Silverstein Properties",
            "building_name": name,
            "address": address,
            "description": desc[:1200],
            "space_type": "Office",
            "floor_suite": floor_suite,
            "size_sqft": single_number(plates),
            "rent": "Upon request" if is_avail else "",
            "contact_role": "Leasing" if c_email else "",
            "contact_name": c_name,
            "contact_email": c_email,
            "contact_phone": "",
            "source_url": "https://www.silversteinproperties.com/properties",
            "scraped_at": scraped_at,
            "neighborhood": "",
            "lat": lat, "lng": lng,
            "image_url": image_url,
        })
        print(f"  {name[:34]:34s} avail={it.get('availability')} geo={'Y' if lat else 'N'} "
              f"img={'Y' if image_url else 'N'}")

    with open("silverstein_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote silverstein_listings.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
