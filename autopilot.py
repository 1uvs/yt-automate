#!/usr/bin/env python3
"""Fully hands-off mode: generate new clips AND auto-schedule them to peak times.

Meant to run on a timer (cron/launchd). Requires a connected channel (token.json
must already exist — run the app and click Connect once first).

    python autopilot.py
"""
from __future__ import annotations

from clipfactory import state, youtube
from clipfactory.config import load_config
from clipfactory.pipeline import run
from clipfactory.publish import publish_clip
from clipfactory.schedule import human, next_slots, to_publish_at


def autopilot() -> None:
    if not youtube.is_connected():
        print("⚠️  Not connected to YouTube (no token.json). Open the app, click "
              "'Connect YouTube channel' once, then autopilot can run on its own.")
        return

    cfg = load_config()
    cap = (cfg.get("autopilot") or {}).get("max_per_run", 8)

    print("=== AUTOPILOT: generating clips ===")
    run()

    pending = state.by_status("pending")[:cap]
    if not pending:
        print("=== AUTOPILOT: nothing new to schedule ===")
        return

    # assign each new clip the next free peak slot (after any already scheduled)
    already = len(state.by_status("scheduled"))
    slots = next_slots(already + len(pending), cfg)[already:]

    print(f"=== AUTOPILOT: scheduling {len(pending)} clip(s) ===")
    for r, slot in zip(pending, slots):
        ok, info = publish_clip(r["clip_id"], cfg, publish_at=to_publish_at(slot))
        status = "✓ scheduled" if ok else "✗ failed"
        extra = f" → {human(slot)}" if ok else f" ({info})"
        print(f"  {status} {r['streamer']}: {r['title'][:40]}{extra}")
    print("=== AUTOPILOT done ===")


if __name__ == "__main__":
    autopilot()
