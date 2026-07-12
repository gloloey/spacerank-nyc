"""
demo_match.py — SpaceRank NYC: run realistic tenant requests through the engine
===============================================================================
Three personas that mirror the target user (international company entering NYC).

Run:  python demo_match.py
"""

import semantic
from matching import TenantRequest, rank_spaces

QUERIES = [
    ("Italian fashion brand: SoHo-style showroom",
     TenantRequest(
         property_type="Showroom",
         size_min=1500, size_max=5000,
         budget_max_psf=60,
         areas=["soho & noho"],
         description="bright loft-style space with exposed brick and high "
                     "ceilings, strong foot traffic, near luxury retail and "
                     "flagship fashion stores")),

    ("German tech company: first US office near Union Square",
     TenantRequest(
         property_type="Office",
         size_min=8000, size_max=20000,
         areas=["flatiron & union square"],
         description="modern open floor plate with natural light for a "
                     "growing engineering team, close to subway lines and "
                     "the downtown tech scene")),

    ("Boutique law firm: prestigious Midtown address on a budget",
     TenantRequest(
         property_type="Office",
         size_min=2000, size_max=6000,
         budget_max_psf=45,
         areas=["penn district & garment"],
         description="professional pre-war building with renovated lobby, "
                     "attended entrance and quick access to Grand Central "
                     "and Penn Station")),
]


def show(title, req, results):
    print("=" * 78)
    print(title)
    print("-" * 78)
    for i, r in enumerate(results, 1):
        size = f"{int(r['size_sqft']):,} sf" if r["size_sqft"] == r["size_sqft"] else "size n/a"
        print(f"{i}. [{r['score']:5.1f}] {r['building']} — {r['suite']} "
              f"({size}, {r['rent']})")
        print(f"     why : {r['reason']}")
        print(f"     who : {r['contact']}")
    print()


if __name__ == "__main__":
    print(f"semantic backend: {semantic.BACKEND}\n")
    for title, req in QUERIES:
        show(title, req, rank_spaces(req, top_n=5))
