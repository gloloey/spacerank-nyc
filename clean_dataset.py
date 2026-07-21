"""
clean_dataset.py — SpaceRank NYC, step 2: raw scrapes -> analysis-ready dataset
===============================================================================
Reads  every *_listings.csv (GFP, Rudin, SL Green, future landlords...)
Plus   manhattan_pluto.csv  (NYC's tax-lot backbone, for enrichment)
Writes spaces_clean.csv — one harmonized table the engine runs on.

WHY THIS STEP EXISTS (the "data engineering" layer):
Every landlord site is messy in its own way. GFP publishes rents but hides
coordinates in an API; Rudin embeds coordinates but no rents or emails;
SL Green has rich unit data and real contacts but NO coordinates at all.
This step absorbs all those differences ONCE.

THE PLUTO JOIN (the backbone earning its keep):
We join scraped buildings to NYC PLUTO **by normalized address** — never by
owner name (95.2% of owner strings own exactly one building; grouping by
them is meaningless). The join supplies:
  * lat/lng for buildings whose sites don't expose coordinates (SL Green)
  * year_built, floors, building class for EVERY matched building
Address normalization handles the real-world mismatches:
  "10 East 53rd Street" -> "10 EAST 53 STREET"   (PLUTO drops ordinals)
  "One Battery Park Plaza" -> "1 BATTERY PARK PLAZA"
  "1560 Broadway - Actors' Equity Building" -> "1560 BROADWAY"

Run:  python clean_dataset.py
"""

import glob
import time
import os
from datetime import datetime, timezone
import json
import re

import pandas as pd

BOROUGH_BY_SLUG = {          # GFP buildings outside Manhattan
    "45-18-court-square-long-island-city": "Queens (Long Island City)",
    "43-01-22nd-street":                   "Queens (Long Island City)",
    "10-27-46th-avenue":                   "Queens (Long Island City)",
    "11-05-44th-drive":                    "Queens (Long Island City)",
    "7-bushwick-place":                    "Brooklyn (Williamsburg)",
    "285-north-6th-street":                "Brooklyn (Williamsburg)",
    "1031-shore-parkway":                  "Brooklyn (Bath Beach)",
    "349-east-149th-street":               "Bronx (Melrose)",
    "150-bay-street":                      "Jersey City, NJ",
    "265-coles-street":                    "Jersey City, NJ",
}
BOROUGH_BY_HOOD = {"Brooklyn": "Brooklyn (Navy Yard)"}
RESIDENTIAL_OR_OTHER = {
    "25-water-street", "301-first-avenue", "1031-shore-parkway",
    "175-madison-avenue", "125-west-3rd-street", "573-hudson-street",
    "71-thomas-street",
}
HOOD_CENTROIDS = {
    "Midtown": (40.7549, -73.9840), "Midtown South": (40.7440, -73.9890),
    "Downtown": (40.7070, -74.0090), "Brooklyn": (40.7008, -73.9723),
    "Upper West Side": (40.7870, -73.9754),
}
WORD_NUMBERS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
                "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "TEN": "10",
                "ELEVEN": "11", "TWELVE": "12"}


def slug_of(url):
    return url.rstrip("/").split("/")[-1]


ORDINAL_AVENUES = {"FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4",
                   "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8",
                   "NINTH": "9", "TENTH": "10", "ELEVENTH": "11", "TWELFTH": "12"}


def normalize_address(name):
    """Make a scraped building name comparable to PLUTO's address format.
    PLUTO writes '520 8 AVENUE', drops ordinal suffixes ('53 STREET'), and
    is inconsistent about 'AVENUE OF (THE) AMERICAS'."""
    a = name.upper().split(" - ")[0].split(",")[0].strip()      # drop nicknames
    a = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", a)             # 53RD -> 53
    a = a.replace("AVENUE OF THE AMERICAS", "AVENUE OF AMERICAS")
    words = a.split()
    if words and words[0] in WORD_NUMBERS:                       # ONE -> 1
        words[0] = WORD_NUMBERS[words[0]]
    # 'EIGHTH AVENUE' -> '8 AVENUE' (but not the leading house number word)
    for i in range(1, len(words)):
        if words[i] in ORDINAL_AVENUES and i + 1 < len(words) and words[i + 1] == "AVENUE":
            words[i] = ORDINAL_AVENUES[words[i]]
    return " ".join(words)


def parse_rent_psf(rent):
    if not isinstance(rent, str):
        return float("nan")
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", rent)
    return float(m.group(1).replace(",", "")) if m else float("nan")


# Commercial listings almost never say "furnished" (that's a residential
# term) — what they DO sometimes say is the fit-out CONDITION, which is the
# honest equivalent: turnkey/pre-built/move-in-ready space has furniture
# and finishes ready to use, raw/whitebox/shell space is an empty box the
# tenant builds out themselves. Only ~16% of listings mention either — the
# rest are left "" (unknown), scored neutrally, never guessed.
_TURNKEY_RE = re.compile(
    r"\bturnkey\b|\bpre-?built\b|\bmove-?in ready\b|\bfurnished\b|"
    r"\bplug[\s-]and[\s-]play\b|\bbuilt-?out\b", re.I)
_RAW_RE = re.compile(r"\braw\b|\bwhite ?box\b|\bshell\b|\bunfurnished\b", re.I)


def parse_fit_condition(description):
    """"turnkey" | "raw" | "" (unknown) — first real signal found, checked
    in this order because a description mentioning both ("raw space that
    can be built out turnkey for you") is describing an offer, not a fact
    about the space's CURRENT condition, and "turnkey" is the more specific,
    tenant-relevant claim in that case."""
    if not isinstance(description, str):
        return ""
    if _TURNKEY_RE.search(description):
        return "turnkey"
    if _RAW_RE.search(description):
        return "raw"
    return ""


def load_pluto():
    """Only the columns we need, keyed by normalized address.
    Returns None when manhattan_pluto.csv is absent (e.g. on a CI runner —
    the 23 MB backbone isn't committed); callers fall back to pluto_cache.json,
    a small committed snapshot of the enrichment produced last time the real
    PLUTO file WAS available. Coordinates and year-built don't drift, so the
    cache is an honest stand-in, and dataset_meta.json records which was used."""
    if not os.path.exists("manhattan_pluto.csv"):
        return None
    pluto = pd.read_csv("manhattan_pluto.csv",
                        usecols=["address", "yearbuilt", "numfloors",
                                 "bldgclass", "latitude", "longitude"],
                        low_memory=False)
    pluto = pluto.dropna(subset=["address"])
    pluto["addr_key"] = (pluto["address"].str.upper().str.strip()
                         .str.replace("AVENUE OF THE AMERICAS",
                                      "AVENUE OF AMERICAS", regex=False))
    return pluto.drop_duplicates("addr_key").set_index("addr_key")


def main():
    frames = [pd.read_csv(f) for f in sorted(glob.glob("*_listings.csv"))]
    df = pd.concat(frames, ignore_index=True)
    with open("building_coords.json", encoding="utf-8") as f:
        coords = json.load(f)                          # GFP: slug -> [lng, lat]
    pluto = load_pluto()

    df["slug"] = df["source_url"].map(slug_of)
    df["addr_key"] = df["building_name"].map(normalize_address)

    # --- PLUTO enrichment (by address, never by owner name) ---------------
    # Two paths, honestly recorded in dataset_meta.json:
    #   * manhattan_pluto.csv present  -> real join, and we SNAPSHOT the
    #     enrichment into pluto_cache.json (small, committed)
    #   * absent (CI runner)           -> reuse that snapshot; coordinates and
    #     year-built don't drift between weekly runs
    cols = [("yearbuilt", "year_built"), ("numfloors", "floors"),
            ("bldgclass", "bldg_class"),
            ("latitude", "pluto_lat"), ("longitude", "pluto_lng")]
    if pluto is not None:
        pluto_source = "pluto_csv"
        matched = df["addr_key"].isin(pluto.index)
        for src, dst in cols:
            df[dst] = df["addr_key"].map(pluto[src])
        cache = {}
        for key in sorted(set(df.loc[matched, "addr_key"])):
            row = pluto.loc[key]
            cache[key] = {src: (None if pd.isna(row[src]) else
                                (float(row[src]) if src != "bldgclass" else str(row[src])))
                          for src, _ in cols}
        with open("pluto_cache.json", "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    else:
        pluto_source = "pluto_cache.json (snapshot — full PLUTO not on this machine)"
        with open("pluto_cache.json", encoding="utf-8") as f:
            cache = json.load(f)
        matched = df["addr_key"].isin(cache)
        for src, dst in cols:
            df[dst] = df["addr_key"].map(lambda k, s=src: cache.get(k, {}).get(s))

    # --- coordinates: scraper-inline > GFP coords file > PLUTO > hood centroid ---
    if "lat" not in df.columns:
        df["lat"] = float("nan"); df["lng"] = float("nan")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lng"] = pd.to_numeric(df["lng"], errors="coerce")
    df["lat"] = df["lat"].fillna(pd.to_numeric(
        df["slug"].map(lambda s: coords.get(s, [None, None])[1]), errors="coerce"))
    df["lng"] = df["lng"].fillna(pd.to_numeric(
        df["slug"].map(lambda s: coords.get(s, [None, None])[0]), errors="coerce"))
    df["lat"] = df["lat"].fillna(df["pluto_lat"])
    df["lng"] = df["lng"].fillna(df["pluto_lng"])

    # --- NYC GeoSearch (free city geocoder, no key) ------------------------
    # Buildings PLUTO can't match (corner lots keyed by the other frontage)
    # get real coordinates from geocode_cache.json — a committed cache built
    # via geosearch.planninglabs.nyc. On machines with internet (CI runners),
    # any NEW un-geocoded building is looked up live and added to the cache;
    # where the API is unreachable we just use the cache. An un-geocodable
    # building still honestly fails area filters rather than being guessed.
    try:
        with open("geocode_cache.json", encoding="utf-8") as f:
            geocache = json.load(f)
    except (OSError, ValueError):
        geocache = {}
    still = df["lat"].isna()
    todo = sorted(set(df.loc[still, "building_name"]) - set(geocache))
    if todo:
        try:
            import requests as _rq
            for name in todo:
                r = _rq.get("https://geosearch.planninglabs.nyc/v2/search",
                            params={"size": 1, "text": f"{name}, New York, NY"},
                            timeout=8)
                feats = r.json().get("features", [])
                if feats:
                    lng_, lat_ = feats[0]["geometry"]["coordinates"]
                    geocache[name] = {"lat": lat_, "lng": lng_}
                time.sleep(0.4)                       # politeness
            with open("geocode_cache.json", "w", encoding="utf-8") as f:
                json.dump(geocache, f, indent=1, sort_keys=True)
        except Exception as e:                        # offline — cache only
            print(f"  (GeoSearch unavailable here: {type(e).__name__} — using cache only)")
    df["lat"] = df["lat"].fillna(df["building_name"].map(
        lambda b: geocache.get(b, {}).get("lat")))
    df["lng"] = df["lng"].fillna(df["building_name"].map(
        lambda b: geocache.get(b, {}).get("lng")))
    if "neighborhood" not in df.columns:
        df["neighborhood"] = ""
    df["lat"] = df["lat"].fillna(pd.to_numeric(df["neighborhood"].map(
        lambda h: HOOD_CENTROIDS.get(h, (None, None))[0]), errors="coerce"))
    df["lng"] = df["lng"].fillna(pd.to_numeric(df["neighborhood"].map(
        lambda h: HOOD_CENTROIDS.get(h, (None, None))[1]), errors="coerce"))
    df = df.drop(columns=["pluto_lat", "pluto_lng"])

    # --- location / use flags ---
    df["borough"] = (df["slug"].map(BOROUGH_BY_SLUG)
                     .fillna(df["neighborhood"].map(BOROUGH_BY_HOOD))
                     .fillna("Manhattan"))
    df["building_use"] = df["slug"].map(
        lambda s: "residential/other" if s in RESIDENTIAL_OR_OTHER else "commercial")

    # --- rent + availability ---
    df["rent_psf"] = df["rent"].map(parse_rent_psf)
    is_space  = df["floor_suite"].notna() & (df["floor_suite"].astype(str).str.strip() != "")
    is_leased = df["rent"].fillna("").astype(str).str.strip().str.lower() == "leased"
    is_sample = df["floor_suite"].fillna("").astype(str).str.contains("Sample", case=False)
    df["is_available"] = is_space & ~is_leased & ~is_sample
    df["size_sqft"] = pd.to_numeric(df["size_sqft"], errors="coerce")
    df["fit_condition"] = df["description"].map(parse_fit_condition)

    df.to_csv("spaces_clean.csv", index=False)

    # --- dataset_meta.json: the freshness stamp the API + UI display -------
    avail = df[df["is_available"]]
    meta = {
        "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_rows": int(len(df)),
        "available_spaces": int(df["is_available"].sum()),
        "buildings": int(df.groupby("building_name").ngroups),
        "buildings_with_availability": int(avail.groupby("building_name").ngroups),
        "landlords": sorted(df["landlord"].dropna().unique().tolist()),
        "per_landlord": {k: {"rows": int(v["count"]), "available": int(v["sum"])}
                         for k, v in df.groupby("landlord")["is_available"]
                                       .agg(["count", "sum"]).iterrows()},
        "pluto_source": pluto_source,
    }
    with open("dataset_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print("wrote dataset_meta.json (refreshed_at " + meta["refreshed_at"] + ")")

    n_bldg = df.groupby("building_name").ngroups
    n_join = df[matched].groupby("building_name").ngroups
    print(f"wrote spaces_clean.csv  ({len(df)} rows from {len(frames)} landlord file(s))")
    print(f"  available spaces      : {int(df['is_available'].sum())}")
    print(f"  with coordinates      : {int(df['lat'].notna().sum())}/{len(df)}")
    print(f"  PLUTO join            : {n_join}/{n_bldg} buildings matched "
          f"(Manhattan-only backbone; outer-borough/JC can't match)")
    print("\nby landlord (rows / available):")
    print(df.groupby('landlord')['is_available'].agg(['count', 'sum']).to_string())
    unmatched = sorted(set(df.loc[~matched & (df['borough'] == 'Manhattan'), 'building_name']))
    if unmatched:
        print("\nManhattan buildings that did NOT match PLUTO:")
        for b in unmatched:
            print("   -", b)


if __name__ == "__main__":
    main()
