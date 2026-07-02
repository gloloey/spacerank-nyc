"""
scrape_gfp.py — SpaceRank NYC: GFP Real Estate scraper
=======================================================
Collects every GFP building (listings + description + ownership-side
contacts) into one flat CSV: gfp_listings.csv (one row per available space).

HOW THE SITE ACTUALLY WORKS (discovered in Phase 1):
  * The building pages are server-rendered HTML for MOST content:
    the description and the "Building Contacts" ARE in the raw HTML.
  * The availabilities table is EMPTY in the raw HTML. JavaScript fills it
    by calling a hidden JSON API:  https://www.gfpre.com/api/property
    That endpoint returns ALL ~59 buildings with ALL current availabilities
    (floor_suite, rsf, rent, possession, term, notes) in ONE response.
  * robots.txt allows everything (checked 2026-07-01).

SO THE STRATEGY IS HYBRID:
  1 API call   -> building list + availabilities        (JSON, no parsing pain)
  ~59 page GETs -> description + full contact list       (BeautifulSoup)

USAGE (from this folder):
  python scrape_gfp.py test   -> scrape ONLY 515 Madison Ave and print it (Phase 2)
  python scrape_gfp.py        -> full run, writes gfp_listings.csv (Phases 3-5)
"""

import csv
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.gfpre.com"
LANDLORD = "GFP Real Estate"

# Identify ourselves. Sites often reject the default "python-requests" agent;
# a browser-like string with an honest project tag is polite and robust.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "SpaceRankNYC-student-project"
}


# ---------------------------------------------------------------------------
# PHASE 3 (via the API): get every building + its availabilities in one call
# ---------------------------------------------------------------------------
def get_all_properties():
    """Return the list of property dicts from GFP's own JSON API.

    Each dict includes: title, slug, url, location, mapbox_center,
    availabilities (list of dicts), assetManager, propertyManager.
    """
    resp = requests.get(f"{BASE}/api/property", headers=HEADERS, timeout=30)
    resp.raise_for_status()                 # crash loudly on HTTP errors
    return resp.json()["data"]              # the payload is {"data": [...]}


# ---------------------------------------------------------------------------
# PHASE 2: scrape ONE building page (description + contacts) with BS4
# ---------------------------------------------------------------------------
def scrape_building(url):
    """Fetch one building page and extract the HTML-only fields.

    Where each field lives in the page (found by inspecting the source):
      building_name -> <h1 class="heading">
      description   -> <div class="property-information"> ... <div class="markdown">
      contacts      -> <div class="contact-cards"> holds one
                       <dl class="contact-card"> per person:
                           <dt>            the role  (e.g. "Asset / Leasing Manager")
                           <dd><a>         the name  (sometimes plain text, no <a>)
                           <dd><a mailto:> the email
                           <dd><a tel:>    the phone
    """
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # "Parsing" = turning the raw HTML string into a tree of Python objects
    # we can search. "lxml" is the engine that does the reading.
    soup = BeautifulSoup(resp.text, "lxml")

    # .select_one(css) returns the FIRST element matching a CSS selector.
    # "h1.heading" means: an <h1> tag whose class includes "heading".
    h1 = soup.select_one("h1.heading")
    building_name = h1.get_text(strip=True) if h1 else ""

    # "div.property-information div.markdown" means: a div.markdown ANYWHERE
    # INSIDE div.property-information (descendant selector).
    desc_el = soup.select_one("div.property-information div.markdown")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    contacts = []
    # .select(css) returns ALL matches as a list.
    for card in soup.select("div.contact-cards dl.contact-card"):
        role_el = card.select_one("dt")
        contact = {
            "role": role_el.get_text(strip=True) if role_el else "",
            "name": "", "email": "", "phone": "",
        }
        for dd in card.select("dd"):
            a = dd.select_one("a")
            href = a.get("href", "") if a else ""
            text = dd.get_text(strip=True)
            if href.startswith("mailto:"):
                contact["email"] = href.removeprefix("mailto:").strip()
            elif href.startswith("tel:"):
                contact["phone"] = text
            elif text:                       # a name (with or without a link)
                contact["name"] = text
        contacts.append(contact)

    return {
        "building_name": building_name,
        "address": building_name,   # GFP names buildings by street address
        "description": description,
        "contacts": contacts,
        "source_url": url,
    }


def primary_contact(contacts):
    """Pick the ownership-side LEASING contact for the CSV row.

    SpaceRank's goal is connecting tenants to the leasing side, so we prefer
    the Asset/Leasing Manager; fall back to Property Manager; else first one.
    """
    for pattern in ("leasing", "asset", "property manager"):
        for c in contacts:
            if pattern in c["role"].lower():
                return c
    return contacts[0] if contacts else {"role": "", "name": "", "email": "", "phone": ""}


# ---------------------------------------------------------------------------
# PHASES 4 + 5: loop over everything, flatten, save CSV
# ---------------------------------------------------------------------------
COLUMNS = ["landlord", "building_name", "address", "description",
           "space_type", "floor_suite", "size_sqft", "rent",
           "contact_role", "contact_name", "contact_email", "contact_phone",
           "source_url", "scraped_at"]


def build_rows(api_prop, page):
    """Merge one building's API data + scraped page into flat CSV rows.

    ONE ROW PER AVAILABLE SPACE; if the building has none, ONE row with the
    space fields left blank (so the building itself still exists in our data).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    contact = primary_contact(page["contacts"])
    base_row = {
        "landlord": LANDLORD,
        "building_name": page["building_name"] or api_prop.get("title", ""),
        "address": page["address"] or api_prop.get("title", ""),
        "description": page["description"],
        "space_type": "", "floor_suite": "", "size_sqft": "", "rent": "",
        "contact_role": contact["role"],
        "contact_name": contact["name"],
        "contact_email": contact["email"],
        "contact_phone": contact["phone"],
        "source_url": page["source_url"],
        "scraped_at": now,
    }

    spaces = api_prop.get("availabilities") or []
    if not spaces:
        return [base_row]

    rows = []
    for sp in spaces:
        row = dict(base_row)                      # copy the building-level info
        row["space_type"] = ", ".join(sp.get("types") or [])
        row["floor_suite"] = sp.get("floor_suite") or ""
        row["size_sqft"] = sp.get("rsf") or ""
        row["rent"] = sp.get("rent") or ""
        rows.append(row)
    return rows


def main(test_only=False):
    print("Fetching building list from the API ...")
    props = get_all_properties()
    print(f"  -> {len(props)} buildings, "
          f"{sum(len(p.get('availabilities') or []) for p in props)} availabilities total")

    if test_only:
        props = [p for p in props if p["slug"] == "515-madison-avenue"]

    all_rows, failures = [], []
    for i, prop in enumerate(props, 1):
        url = prop.get("url") or f"{BASE}/properties/{prop['slug']}"
        try:
            page = scrape_building(url)
            rows = build_rows(prop, page)
            all_rows.extend(rows)
            print(f"[{i:2d}/{len(props)}] {prop['title']:45.45s} "
                  f"spaces={len(prop.get('availabilities') or []):3d} "
                  f"contacts={len(page['contacts'])}")
        except Exception as e:
            # One broken page must not kill a 2-minute run: note it, move on.
            failures.append((url, repr(e)))
            print(f"[{i:2d}/{len(props)}] FAILED {url} -> {e!r}")
        time.sleep(2)                              # politeness pause

    if test_only:
        # Phase 2: print everything so it can be eyeballed against the site.
        import json
        print("\n--- extracted building ---")
        print(json.dumps(all_rows, indent=2)[:6000])
        return

    # ---- PHASE 5: save + summary ----
    out = "gfp_listings.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    n_avail = sum(1 for r in all_rows if r["floor_suite"])
    n_email = sum(1 for r in all_rows if r["contact_email"])
    print(f"\nSaved {out}")
    print(f"  buildings scraped : {len(props) - len(failures)} of {len(props)}")
    print(f"  total rows        : {len(all_rows)}")
    print(f"  rows w/ email     : {n_email}")
    print(f"  rows w/ a space   : {n_avail}")
    if failures:
        print("  failures:")
        for u, e in failures:
            print("   -", u, e)


if __name__ == "__main__":
    main(test_only=(len(sys.argv) > 1 and sys.argv[1] == "test"))
