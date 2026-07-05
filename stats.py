#!/usr/bin/env python3
"""Show which A/B variant is winning on your channel (from real YouTube Analytics).

    python stats.py
"""
from clipfactory.analytics import variant_leaderboard

if __name__ == "__main__":
    try:
        board, per = variant_leaderboard()
    except Exception as e:
        print(f"Could not fetch analytics: {e}")
        print("Make sure the YouTube Analytics API is enabled and you've re-connected "
              "your channel (delete token.json, then Connect again).")
        raise SystemExit(1)

    if not per:
        print("No published clips yet. Publish a few (in both formats), then check back "
              "in a day or two — YouTube Analytics needs time to populate.")
        raise SystemExit(0)

    print("\n🏆 VARIANT LEADERBOARD  (best-performing format on your channel)\n")
    for i, b in enumerate(board):
        crown = "👑 " if i == 0 else "   "
        print(f"  {crown}{b['variant']:>6}: {b['clips']} clip(s) | "
              f"avg {b['avg_views']} views | {b['avg_retention']}% avg retention")

    print("\n  Per video:")
    for p in per:
        print(f"    [{p['variant']:>6}] {p['views']:>6} views  {p['retention']:>5}%  {p['title'][:44]}")
    print()
