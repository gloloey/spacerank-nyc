"""
tools/run_search_alerts.py — SpaceRank NYC: weekly saved-search alert digest
==============================================================================
Run from .github/workflows/search_alerts.yml, right after refresh_data.yml
commits a fresh spaces_clean.csv. Does three things:

  1. Computes a stable identity key for every listing currently in the
     dataset and diffs it against db.known_listings (a plain persisted
     memory of every key ever seen — the CSV itself is rebuilt from
     scratch each week and has no history of its own). Whatever's new
     THIS run is "new since last week".
  2. For every active saved search (db.saved_searches), reconstructs a
     TenantRequest from its stored params and ranks the FULL current
     dataset, then keeps only the results whose key is in the "new"
     set from step 1.
  3. Emails a digest for any search with at least one new match, via
     email_notify.send_search_alert(), and marks it notified.

Safe to run with nothing configured: db.py's functions all degrade to
empty/no-op without DATABASE_URL, so this exits quietly (0 new listings,
0 searches) rather than failing the whole workflow. Needs DATABASE_URL and
RESEND_API_KEY as GitHub Actions repo secrets to actually do anything —
see .github/workflows/search_alerts.yml's comment for the one-time setup.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import db
import email_notify
from matching import TenantRequest, rank_spaces

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.environ.get("SITE_BASE_URL", "https://spaceranknyc.com")


def listing_key_from_row(row) -> str:
    """MUST produce the same key for the same physical listing whether it
    comes from the raw CSV (this function) or from a rank_spaces() result
    dict (listing_key_from_result below) — that's the whole diff mechanism."""
    return f"{row['landlord']}|{row['building_name']}|{row['floor_suite']}|{row['source_url']}"


def listing_key_from_result(r: dict) -> str:
    return f"{r['landlord']}|{r['building']}|{r['suite']}|{r['url']}"


def request_from_params(params: dict) -> TenantRequest:
    anchor = None
    if params.get("anchor_lat") and params.get("anchor_lng"):
        anchor = {"lat": params["anchor_lat"], "lng": params["anchor_lng"],
                 "label": params.get("anchor_label", "")}
        if params.get("anchor_radius_mi"):
            anchor["radius_mi"] = params["anchor_radius_mi"]
    areas = params.get("area") or []
    if isinstance(areas, str):
        areas = [areas]
    return TenantRequest(
        property_type=params.get("property_type", "Office"),
        size_min=params.get("size_min"), size_max=params.get("size_max"),
        budget_max_psf=params.get("budget"),
        areas=areas, description=params.get("q", ""),
        landlord_style=params.get("landlord_style"), term=params.get("term"),
        fit_preference=params.get("fit"), anchor=anchor,
    )


def search_label(params: dict) -> str:
    bits = [params.get("property_type", "Office")]
    if params.get("size_min") or params.get("size_max"):
        bits.append(f"{params.get('size_min', '?')}-{params.get('size_max', '?')} sf")
    if params.get("budget"):
        bits.append(f"≤ ${params['budget']}/SF")
    areas = params.get("area") or []
    if isinstance(areas, str):
        areas = [areas]
    bits.extend(areas)
    if params.get("q"):
        bits.append(f'"{params["q"][:40]}"')
    return " · ".join(bits)


def main():
    csv_path = os.path.join(HERE, "spaces_clean.csv")
    df = pd.read_csv(csv_path)
    current_keys = [listing_key_from_row(row) for _, row in df.iterrows()]
    new_keys = set(db.mark_new_listing_keys(current_keys))
    print(f"Listings this run: {len(current_keys)} total, {len(new_keys)} new since last run.")
    if not new_keys:
        print("Nothing new — no alerts to send.")
        return

    searches = db.fetch_active_saved_searches()
    print(f"Active saved searches: {len(searches)}")
    sent = 0
    for s in searches:
        req = request_from_params(s["params"])
        results = rank_spaces(req, top_n=10**9, csv_path=csv_path)
        fresh = [r for r in results if listing_key_from_result(r) in new_keys]
        if not fresh:
            continue
        unsubscribe_url = f"{BASE_URL.rstrip('/')}/api/alerts/unsubscribe?token={s['token']}"
        ok = email_notify.send_search_alert(s["email"], fresh, search_label(s["params"]), unsubscribe_url)
        if ok:
            db.mark_search_notified(s["token"])
            sent += 1
            print(f"  -> {s['email']}: {len(fresh)} new match(es)")
        else:
            print(f"  -> {s['email']}: {len(fresh)} new match(es), EMAIL FAILED (or RESEND_API_KEY unset)")
    print(f"Alerts sent: {sent}/{len(searches)} searches had new matches.")


if __name__ == "__main__":
    main()
