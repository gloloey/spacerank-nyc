"""
scrape_slgreen.py — SpaceRank NYC: SL Green scraper (landlord #3)
=================================================================
SL Green ("NYC's largest commercial landlord") runs a WordPress/Divi site
with a THIRD architecture — each landlord so far has taught a new pattern:

  GFP:      data behind a JSON API, contacts in HTML
  Rudin:    everything server-rendered, no emails, coords in the page
  SL Green: server-rendered, RICH unit tables (rent/term/occupancy/comments),
            real @slgreen.com leasing contacts, but NO coordinates
            (clean_dataset.py fills those from PLUTO by address)

QUIRKS HANDLED HERE:
  * The property list at /properties/ holds 66 entries, but only ~31 point to
    slgreen.com pages — the rest are external marketing sites (onevanderbilt
    .com etc.), which we skip and document.
  * Each building's "Available Units" section renders the same table three
    times (desktop / mobile / expanded details) -> units must be DEDUPED:
    keep named-suite rows first, then blank-suite rows only if their RSF
    wasn't already seen.
  * Contacts mix SL Green staff with Cushman & Wakefield brokers. Per the
    project's ownership-side rule we keep ONLY @slgreen.com people.

Run:  python scrape_slgreen.py     -> slgreen_listings.csv
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://slgreen.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "SpaceRankNYC-student-project"}


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def property_pages():
    """The filter dropdown on /properties/ lists every building + its link."""
    soup = BeautifulSoup(get(f"{BASE}/properties/"), "lxml")
    out, seen = [], set()
    for li in soup.select(".filter-property-list li[data-link]"):
        link = li.get("data-link", "")
        if "slgreen.com/properties/" in link and link not in seen:
            seen.add(link)
            out.append({"name": li.get_text(strip=True), "link": link})
    return out


def parse_units(soup):
    """Units live in .spaces__wrapper as label:value text runs. We split the
    text on 'RSF/SF:' and regex each chunk — more robust than relying on the
    triple-rendered table structure."""
    wrapper = soup.select_one(".spaces__wrapper")
    if wrapper is None:
        return []
    text = re.sub(r"\s+", " ", wrapper.get_text(" "))
    raw_units = []
    for chunk in re.split(r"RSF/SF:\s*", text)[1:]:
        def g(pattern):
            m = re.search(pattern, chunk)
            return m.group(1).strip() if m else ""
        raw_units.append({
            "rsf": g(r"^([\d,]+)"),
            "suite": g(r"Room/Suite:\s*(.*?)\s*Occupancy:"),
            "rent": g(r"Rent:\s*(.*?)\s*Term:"),
            "type": g(r"Office/Retail:\s*(Office|Retail)") or "Office",
        })
    # dedupe (desktop/mobile/details render the same units repeatedly)
    out, seen_key, seen_rsf = [], set(), set()
    for u in [u for u in raw_units if u["suite"]]:
        key = (u["suite"], u["rsf"])
        if key not in seen_key:
            seen_key.add(key)
            seen_rsf.add(u["rsf"])
            out.append(u)
    for u in [u for u in raw_units if not u["suite"]]:
        if u["rsf"] not in seen_rsf:
            seen_rsf.add(u["rsf"])
            out.append(u)
    return out


def scrape_building(page):
    soup = BeautifulSoup(get(page["link"]), "lxml")
    desc = " ".join(p.get_text(" ", strip=True)
                    for p in soup.select(".et_pb_post_content p")
                    if len(p.get_text(strip=True)) > 40)
    contact = {"email": "", "phone": ""}
    for gi in soup.select(".contact__wrapper .grid-item"):
        a = gi.select_one('a[href^="mailto:"]')
        email = a.get("href", "")[7:] if a else ""
        if email.lower().endswith("slgreen.com"):        # ownership-side only
            phone = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", gi.get_text(" "))
            contact = {"email": email, "phone": phone.group(0) if phone else ""}
            break
    return {"desc": re.sub(r"\s+", " ", desc), "contact": contact,
            "units": parse_units(soup)}


COLUMNS = ["landlord", "building_name", "address", "description", "space_type",
           "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
           "contact_email", "contact_phone", "source_url", "scraped_at",
           "neighborhood", "lat", "lng"]


def name_from_email(email):
    """'larry.swiger@slgreen.com' -> 'Larry Swiger' (honest, derivable)."""
    return " ".join(p.capitalize() for p in email.split("@")[0].split("."))


def main():
    pages = property_pages()
    print(f"{len(pages)} SL Green property pages found")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, failures = [], []
    for i, page in enumerate(pages, 1):
        try:
            b = scrape_building(page)
        except Exception as e:
            failures.append((page["link"], repr(e)))
            print(f"[{i:2d}] FAILED {page['link']} -> {e!r}")
            continue
        c = b["contact"]
        base = {
            "landlord": "SL Green", "building_name": page["name"],
            "address": page["name"], "description": b["desc"],
            "space_type": "", "floor_suite": "", "size_sqft": "", "rent": "",
            "contact_role": "Leasing (SL Green)",
            "contact_name": name_from_email(c["email"]) if c["email"] else "",
            "contact_email": c["email"], "contact_phone": c["phone"],
            "source_url": page["link"], "scraped_at": now,
            "neighborhood": "", "lat": "", "lng": "",
        }
        units = b["units"]
        if not units:
            rows.append(base)
        for u in units:
            r = dict(base)
            r["space_type"] = u["type"]
            r["floor_suite"] = u["suite"]
            r["size_sqft"] = int(u["rsf"].replace(",", "")) if u["rsf"] else ""
            r["rent"] = "" if re.search(r"upon request", u["rent"], re.I) else u["rent"]
            rows.append(r)
        print(f"[{i:2d}/{len(pages)}] {page['name']:38.38s} units={len(units)}")
        time.sleep(2)

    with open("slgreen_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"saved slgreen_listings.csv ({len(rows)} rows, {len(failures)} failures)")


if __name__ == "__main__":
    main()
