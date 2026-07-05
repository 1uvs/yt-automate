#!/usr/bin/env python3
"""Review queue: approve rendered clips to publish them to YouTube.

Usage:
    python review.py list                 # show pending clips
    python review.py open <clip_id>       # preview the rendered mp4
    python review.py approve <clip_id>    # publish to YouTube
    python review.py reject <clip_id>     # drop it
    python review.py approve-all          # publish every pending clip
"""
import subprocess
import sys

from clipfactory import state
from clipfactory.config import load_config
from clipfactory.publish import load_meta as _meta
from clipfactory.publish import publish_clip


def cmd_list():
    rows = state.by_status("pending")
    if not rows:
        print("Review queue is empty.")
        return
    print(f"{len(rows)} clip(s) pending:\n")
    for r in rows:
        m = _meta(r["clip_id"])
        print(f"  [{r['clip_id']}]  {r['streamer']}  ({r['view_count']} views)")
        print(f"     title: {m.get('title', r['title'])}")
        print(f"     file : {r['output_path']}\n")


def cmd_open(cid: str):
    r = state.get(cid)
    if not r or not r["output_path"]:
        print("Not found.")
        return
    subprocess.run(["open", r["output_path"]])


def _publish(cid: str, cfg: dict) -> bool:
    ok, info = publish_clip(cid, cfg)
    if ok:
        print(f"  ✓ published {cid} -> https://youtube.com/watch?v={info}")
    else:
        print(f"  x {cid}: {info}")
    return ok


def cmd_approve(cid: str):
    _publish(cid, load_config())


def cmd_approve_all():
    cfg = load_config()
    rows = state.by_status("pending")
    print(f"Publishing {len(rows)} clip(s)...")
    for r in rows:
        _publish(r["clip_id"], cfg)


def cmd_reject(cid: str):
    state.upsert(cid, status="rejected")
    print(f"Rejected {cid}.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "list":
        cmd_list()
    elif cmd == "open" and arg:
        cmd_open(arg)
    elif cmd == "approve" and arg:
        cmd_approve(arg)
    elif cmd == "approve-all":
        cmd_approve_all()
    elif cmd == "reject" and arg:
        cmd_reject(arg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
