"""
tools/build_subway_stations.py — the OFFLINE half of the "near a subway
station" search feature
=============================================================================
Fetches the MTA's own public subway-station dataset (NY State Open Data,
Socrata dataset id 39hk-dx4f — one of the possible "MTA Subway Stations"
datasets on data.ny.gov; verified by hand to have gtfs_latitude/longitude,
stop_name, complex_id, daytime_routes columns) and collapses it from
"one row per (station, line)" to ONE row per physical station COMPLEX
(several lines sharing a transfer point, e.g. Times Sq-42 St, are one
place a tenant would say "near"), keyed by the MTA's own complex_id so
re-running this script is a no-op unless the MTA dataset itself changes.

Produces subway_stations.json — a small, committed snapshot (same pattern
as pluto_cache.json / geocode_cache.json): station names/coordinates never
drift, so there's no need to re-fetch this on every data refresh, only
when a new subway station opens (rare — re-run by hand when that happens).

Run:  python tools/build_subway_stations.py     (from the repo root)
"""

import json
import os

import requests

API = "https://data.ny.gov/resource/39hk-dx4f.json?$limit=5000"
HEADERS = {"User-Agent": "SpaceRankNYC/0.10 (student project; polite fetch)"}
BOROUGH_NAMES = {"M": "Manhattan", "Bk": "Brooklyn", "Q": "Queens",
                 "Bx": "Bronx", "SI": "Staten Island"}
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    r = requests.get(API, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = r.json()

    by_complex = {}
    for row in rows:
        cid = row.get("complex_id")
        lat, lng = row.get("gtfs_latitude"), row.get("gtfs_longitude")
        if not (cid and lat and lng):
            continue
        entry = by_complex.setdefault(cid, {
            "id": cid,
            "name": row.get("stop_name", "").strip(),
            "borough": BOROUGH_NAMES.get(row.get("borough", ""), row.get("borough", "")),
            "lat": float(lat), "lng": float(lng),
            "routes": set(),
        })
        for route in (row.get("daytime_routes") or "").split():
            entry["routes"].add(route)

    stations = []
    for entry in by_complex.values():
        entry["routes"] = " ".join(sorted(entry["routes"]))
        stations.append(entry)
    stations.sort(key=lambda s: s["name"])

    out_path = os.path.join(HERE, "subway_stations.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stations, f, indent=1, ensure_ascii=False)
    print(f"wrote subway_stations.json ({len(stations)} station complexes, "
          f"source: {len(rows)} MTA rows)")


if __name__ == "__main__":
    main()
