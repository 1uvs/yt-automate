#!/usr/bin/env python3
"""Your virality coach: what's driving views + subscribers, and what to do next.

    python stats.py
"""
from clipfactory.analytics import insights


def _table(title, rows):
    if not rows:
        return
    print(f"\n  {title}")
    print(f"    {'name':<14}{'subs/clip':>10}{'avg views':>11}{'retention':>11}{'clips':>7}")
    for i, b in enumerate(rows):
        crown = "👑" if i == 0 else "  "
        print(f"  {crown}{b['name']:<14}{b['subs_per_clip']:>10}{b['avg_views']:>11}"
              f"{str(b['avg_retention'])+'%':>11}{b['clips']:>7}")


if __name__ == "__main__":
    try:
        d = insights()
    except Exception as e:
        print(f"Could not fetch analytics: {e}")
        print("→ Enable the YouTube Analytics API in Google Cloud and re-connect your channel.")
        raise SystemExit(1)

    print(f"\n📊 {d['clips']} published clip(s) · {d['total_subs']} subscribers gained")

    print("\n🧠 COACH:")
    for rec in d["recommendations"]:
        print(f"   • {rec}")

    _table("BY FORMAT", d["by_format"])
    _table("BY STREAMER", d["by_streamer"])
    _table("BY LENGTH", d["by_length"])

    if d["by_hashtag"]:
        print("\n  BEST HASHTAGS (auto-boosted on new clips):")
        for h in d["by_hashtag"][:8]:
            print(f"    #{h['tag']:<16} {h['subs_per_clip']} subs/clip · {h['avg_views']} avg views")

    # The evolving strategy (written by the GPT strategy brain, if it's run).
    from clipfactory.strategy import load_strategy
    s = load_strategy()
    if s:
        print(f"\n🧬 EVOLVING STRATEGY (updated {s.get('updated', '?')}, "
              f"from {s.get('based_on_clips', '?')} clips):")
        if s.get("rationale"):
            print(f"   {s['rationale']}")
        for f in (s.get("title_formulas") or [])[:5]:
            print(f"    • title: {f}")
        if s.get("hook_style"):
            print(f"    • hook: {s['hook_style']}")
        if s.get("priority_streamers"):
            print(f"    • prioritize: {', '.join(s['priority_streamers'][:8])}")
    print()
