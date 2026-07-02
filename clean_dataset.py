"""
clean_dataset.py — SpaceRank NYC, step 2: raw scrapes -> analysis-ready dataset
===============================================================================
Reads  gfp_listings.csv + rudin_listings.csv (+ any future *_listings.csv)
Writes spaces_clean.csv — one harmonized table the engine runs on.

WHY THIS STEP EXISTS (the "data engineering" layer):
Every landlord site is messy in its own way. GFP publishes rents as strings
("$40.00 PSF" or "Upon Request") and contacts in HTML but hides coordinates
in an API; Rudin publishes no rents and no emails but embeds coordinates and
neighborhoods. This step absorbs ALL those differences ONCE, so the matching
engine sees a single clean schema.

Run:  python clean_dataset.py
"""

import glob
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
BOROUGH_BY_HOOD = {          # Rudin cards carry a neighborhood instead
    "Brooklyn": "Brooklyn (Navy Yard)",
}
RESIDENTIAL_OR_OTHER = {     # not leasable commercial space right now (GFP)
    "25-water-street", "301-first-avenue", "1031-shore-parkway",
    "175-madison-avenue", "125-west-3rd-street", "573-hudson-street",
    "71-thomas-street",
}
# fallback centroids when a building page had no usable map pin
HOOD_CENTROIDS = {
    "Midtown": (40.7549, -73.9840), "Midtown South": (40.7440, -73.9890),
    "Downtown": (40.7070, -74.0090), "Brooklyn": (40.7008, -73.9723),
    "Upper West Side": (40.7870, -73.9754),
}


def slug_of(url):
    return url.rstrip("/").split("/")[-1]


def parse_rent_psf(rent):
    """'$40.00 PSF' -> 40.0; 'Upon request'/'Leased'/blank -> NaN (unknown)."""
    if not isinstance(rent, str):
        return float("nan")
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", rent)
    return float(m.group(1).replace(",", "")) if m else float("nan")


def main():
    frames = [pd.read_csv(f) for f in sorted(glob.glob("*_listings.csv"))]
    df = pd.concat(frames, ignore_index=True)          # harmonize: missing cols -> NaN
    with open("building_coords.json", encoding="utf-8") as f:
        coords = json.load(f)                          # GFP: slug -> [lng, lat]

    df["slug"] = df["source_url"].map(slug_of)

    # --- coordinates: inline (Rudin) > coords file (GFP) > neighborhood centroid ---
    if "lat" not in df.columns:
        df["lat"] = float("nan")
        df["lng"] = float("nan")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lng"] = pd.to_numeric(df["lng"], errors="coerce")
    from_file_lat = df["slug"].map(lambda s: coords.get(s, [None, None])[1])
    from_file_lng = df["slug"].map(lambda s: coords.get(s, [None, None])[0])
    df["lat"] = df["lat"].fillna(pd.to_numeric(from_file_lat, errors="coerce"))
    df["lng"] = df["lng"].fillna(pd.to_numeric(from_file_lng, errors="coerce"))
    if "neighborhood" not in df.columns:
        df["neighborhood"] = ""
    hood_lat = df["neighborhood"].map(lambda h: HOOD_CENTROIDS.get(h, (None, None))[0])
    hood_lng = df["neighborhood"].map(lambda h: HOOD_CENTROIDS.get(h, (None, None))[1])
    df["lat"] = df["lat"].fillna(pd.to_numeric(hood_lat, errors="coerce"))
    df["lng"] = df["lng"].fillna(pd.to_numeric(hood_lng, errors="coerce"))

    # --- location / use flags ---
    df["borough"] = (df["slug"].map(BOROUGH_BY_SLUG)
                     .fillna(df["neighborhood"].map(BOROUGH_BY_HOOD))
                     .fillna("Manhattan"))
    df["building_use"] = df["slug"].map(
        lambda s: "residential/other" if s in RESIDENTIAL_OR_OTHER else "commercial")

    # --- rent: raw string kept, numeric column added ---
    df["rent_psf"] = df["rent"].map(parse_rent_psf)

    # --- availability flag ---
    is_space  = df["floor_suite"].notna() & (df["floor_suite"].astype(str).str.strip() != "")
    is_leased = df["rent"].fillna("").astype(str).str.strip().str.lower() == "leased"
    is_sample = df["floor_suite"].fillna("").astype(str).str.contains("Sample", case=False)
    df["is_available"] = is_space & ~is_leased & ~is_sample

    df["size_sqft"] = pd.to_numeric(df["size_sqft"], errors="coerce")

    df.to_csv("spaces_clean.csv", index=False)

    print(f"wrote spaces_clean.csv  ({len(df)} rows from {len(frames)} landlord file(s))")
    print(f"  available spaces      : {int(df['is_available'].sum())}")
    print(f"  with numeric rent     : {int(df['rent_psf'].notna().sum())}")
    print(f"  with coordinates      : {int(df['lat'].notna().sum())}")
    print("\nby landlord:")
    print(df.groupby('landlord')['is_available'].agg(['count', 'sum']).to_string())


if __name__ == "__main__":
    main()
