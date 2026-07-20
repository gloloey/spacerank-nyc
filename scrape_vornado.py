"""
scrape_vornado.py — landlord #4: Vornado Realty Trust (vno.com)
================================================================
ARCHITECTURE (scouted 2026-07-20 via the browser):
  * /portfolio/office and /portfolio/street-retail list property landing
    pages at /office/property/{slug}/{id}/landing (and /street-retail/...).
  * Each landing page is SERVER-RENDERED and self-contained:
      - availability table  #w_portfolio_availabilities_* tbody tr
        columns: Floor | Location ("ENTIRE 8TH FLOOR") | SQ FT | Available
        (rows repeat for responsive variants -> dedupe on content)
      - coordinates embedded as data-latitude / data-longitude (their own
        map widget's center — no geocoding needed)
      - leasing contacts as mailto: links @vno.com (ownership-side ✓)
      - building description in the #overview section
      - photo via og:image
  * No rents published anywhere -> "Upon request", honestly.
  * The portfolio is national (555 California, THE MART) — we keep only
    properties whose embedded coordinates fall inside the NYC bounding box.

Vornado is a public REIT (NYSE: VNO) -> landlord_style "institutional".
Run on a machine with normal internet (GitHub runner / laptop).
"""

import csv
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.vno.com"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite scraper)"}
DELAY = 0.6
NYC = {"lat": (40.45, 41.0), "lng": (-74.3, -73.6)}

FIELDS = ["landlord", "building_name", "address", "description", "space_type",
          "floor_suite", "size_sqft", "rent", "contact_role", "contact_name",
          "contact_email", "contact_phone", "source_url", "scraped_at",
          "neighborhood", "lat", "lng"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def property_urls():
    out = []
    for portfolio, kind in [("/portfolio/office", "Office"),
                            ("/portfolio/street-retail", "Retail")]:
        soup = BeautifulSoup(get(BASE + portfolio), "lxml")
        seen = set()
        for a in soup.select("a[href]"):
            h = a["href"]
            if re.match(r"^/(office|street-retail)/property/.+/landing$", h) and h not in seen:
                seen.add(h)
                out.append((BASE + h, kind))
    return out


def parse_property(url, kind):
    html = get(url)
    soup = BeautifulSoup(html, "lxml")

    lat = lng = None
    m = re.search(r'data-latitude="(-?\d+\.\d+)"', html)
    n = re.search(r'data-longitude="(-?\d+\.\d+)"', html)
    if m and n:
        lat, lng = float(m.group(1)), float(n.group(1))
    if not (lat and NYC["lat"][0] < lat < NYC["lat"][1]
            and NYC["lng"][0] < lng < NYC["lng"][1]):
        return []                                   # not a New York property

    title = soup.title.get_text() if soup.title else ""
    parts = [p.strip() for p in title.split("|")]
    name = parts[1] if len(parts) > 2 else url.rstrip("/").split("/")[-3].replace("-", " ").title()

    # description: the overview section, minus nav noise
    ov = soup.select_one("#overview")
    desc = re.sub(r"\s+", " ", ov.get_text(" ", strip=True))[:1500] if ov else ""

    # first ownership-side contact (mailto @vno.com); name = text before the
    # email inside the same block
    c_name = c_email = ""
    for a in soup.select("a[href^='mailto:']"):
        em = a["href"].replace("mailto:", "").split("?")[0].strip()
        if em.endswith("@vno.com"):
            c_email = em
            block = a.find_parent(["div", "li"])
            if block:
                txt = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
                c_name = txt.split(em)[0].strip(" -–|,")[:60]
            break

    rows, seen = [], set()
    for tr in soup.select("[id^=w_portfolio_availabilities] tbody tr"):
        tds = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.select("td")]
        if len(tds) < 4:
            continue
        floor, location, sqft, avail = tds[0], tds[1], tds[2], tds[3]
        key = (floor, location, sqft)
        if key in seen or not (location or sqft):
            continue
        seen.add(key)
        sq = re.sub(r"[^\d]", "", sqft)
        rows.append({
            "landlord": "Vornado Realty Trust",
            "building_name": name,
            "address": name,          # VNO names are addresses or brands; coords are exact anyway
            "description": desc,
            "space_type": kind,
            "floor_suite": location or f"Floor {floor}",
            "size_sqft": sq or "",
            "rent": "Upon request",
            "contact_role": "Leasing" if c_email else "",
            "contact_name": c_name,
            "contact_email": c_email,
            "contact_phone": "",
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "neighborhood": "",
            "lat": lat, "lng": lng,
        })
    # a building with no availabilities still contributes one portfolio row
    if not rows:
        rows.append({**{f: "" for f in FIELDS},
                     "landlord": "Vornado Realty Trust", "building_name": name,
                     "address": name, "description": desc, "space_type": kind,
                     "rent": "", "source_url": url,
                     "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "lat": lat, "lng": lng})
    return rows


def main():
    urls = property_urls()
    print(f"{len(urls)} Vornado property pages (office + street retail)")
    all_rows = []
    for url, kind in urls:
        try:
            got = parse_property(url, kind)
            all_rows.extend(got)
            if got:
                print(f"  {got[0]['building_name'][:40]:40s} {kind:7s} "
                      f"{sum(1 for r in got if r['floor_suite'])} spaces")
        except Exception as e:
            print(f"  !! {url}: {type(e).__name__} {e}")
    with open("vornado_listings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote vornado_listings.csv ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
