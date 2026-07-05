#!/usr/bin/env python3
"""clip-factory web UI — connect your YouTube channel and run everything with buttons.

    python app.py      then open http://localhost:5000
"""
from __future__ import annotations

import contextlib
import io
import threading
import time

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from clipfactory import state, youtube
from clipfactory.config import OUTPUT_DIR, load_config
from clipfactory.pipeline import run as pipeline_run
from clipfactory.publish import load_meta, publish_clip, save_meta
from clipfactory.schedule import human, next_slots, to_publish_at

app = Flask(__name__)

# --- background run state -------------------------------------------------
RUN = {"running": False, "log": [], "started_at": 0.0}


class _Tee(io.StringIO):
    def write(self, s):
        for line in s.splitlines():
            if line.strip():
                RUN["log"].append(line)
        RUN["log"][:] = RUN["log"][-200:]
        return len(s)


def _do_run(streamers):
    RUN.update(running=True, log=[], started_at=time.time())
    try:
        with contextlib.redirect_stdout(_Tee()):
            pipeline_run(limit_streamers=streamers or None)
    except Exception as e:
        RUN["log"].append(f"ERROR: {e}")
    finally:
        RUN["running"] = False


# --- pages ----------------------------------------------------------------
@app.route("/")
def index():
    from clipfactory.schedule import reconcile_scheduled
    reconcile_scheduled()
    channel = None
    if youtube.is_connected():
        try:
            channel = youtube.channel_info()
        except Exception:
            channel = {"title": "connected", "id": "", "subscribers": ""}
    pending = [dict(r) for r in state.by_status("pending")]
    for r in pending:
        r["meta"] = load_meta(r["clip_id"])
    published = [dict(r) for r in state.by_status("published")]
    scheduled = [dict(r) for r in state.by_status("scheduled")]
    return render_template(
        "index.html",
        channel=channel,
        has_secret=youtube.has_client_secret(),
        pending=pending,
        published=published,
        scheduled=scheduled,
        cfg=load_config(),
    )


@app.route("/connect")
def connect():
    try:
        youtube.connect()
    except Exception as e:
        return f"<h3>Could not connect YouTube</h3><p>{e}</p><a href='/'>back</a>", 500
    return redirect(url_for("index"))


@app.route("/run", methods=["POST"])
def run():
    if RUN["running"]:
        return jsonify(ok=False, msg="already running")
    streamers = (request.json or {}).get("streamers") if request.is_json else None
    threading.Thread(target=_do_run, args=(streamers,), daemon=True).start()
    return jsonify(ok=True)


@app.route("/status")
def status():
    return jsonify(
        running=RUN["running"],
        log=RUN["log"][-40:],
        pending=len(state.by_status("pending")),
    )


@app.route("/approve/<cid>", methods=["POST"])
def approve(cid):
    ok, info = publish_clip(cid, load_config())
    url = f"https://youtube.com/watch?v={info}" if ok else ""
    return jsonify(ok=ok, url=url, msg=info)


@app.route("/schedule/<cid>", methods=["POST"])
def schedule_one(cid):
    cfg = load_config()
    # next free slot = one past however many are already scheduled
    already = len(state.by_status("scheduled"))
    slot = next_slots(already + 1, cfg)[-1]
    ok, info = publish_clip(cid, cfg, publish_at=to_publish_at(slot))
    return jsonify(ok=ok, when=human(slot), msg=info)


@app.route("/reject/<cid>", methods=["POST"])
def reject(cid):
    state.upsert(cid, status="rejected")
    return jsonify(ok=True)


@app.route("/update/<cid>", methods=["POST"])
def update(cid):
    m = load_meta(cid)
    data = request.json or {}
    m["title"] = data.get("title", m.get("title", ""))
    m["description"] = data.get("description", m.get("description", ""))
    save_meta(cid, m)
    state.upsert(cid, title=m["title"])
    return jsonify(ok=True)


@app.route("/stats")
def stats():
    from clipfactory.analytics import insights
    try:
        return jsonify(ok=True, data=insights())
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


@app.route("/media/<path:fn>")
def media(fn):
    return send_from_directory(OUTPUT_DIR, fn)


if __name__ == "__main__":
    # Port 5000 is taken by macOS AirPlay Receiver, so use 5050.
    PORT = 5050
    print(f"\n  clip-factory UI → http://localhost:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=False)
