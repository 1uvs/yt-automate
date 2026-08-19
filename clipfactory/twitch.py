"""Fetch top clips from Twitch via the Helix API (App Access Token / client-credentials)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from .config import env
from .retry import with_retry

HELIX = "https://api.twitch.tv/helix"
_token_cache: dict = {"token": None, "exp": 0}


def _app_token() -> str:
    cid, secret = env("TWITCH_CLIENT_ID"), env("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env")
    if _token_cache["token"] and _token_cache["exp"] > time.time() + 60:
        return _token_cache["token"]

    def fetch():
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    data = with_retry(fetch, attempts=3, label="twitch auth")
    _token_cache.update(token=data["access_token"], exp=time.time() + data["expires_in"])
    return data["access_token"]


def _headers() -> dict:
    return {"Client-Id": env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {_app_token()}"}


def _get(path: str, **kwargs) -> dict:
    """GET a Helix endpoint with retries — discovery runs many of these in parallel
    and a single 429/blip shouldn't cost a streamer's whole clip list."""
    def call():
        r = requests.get(f"{HELIX}{path}", headers=_headers(), timeout=30, **kwargs)
        r.raise_for_status()
        return r.json()

    return with_retry(call, attempts=3, label=f"twitch {path}", quiet=True)


def resolve_user_ids(logins: list[str]) -> dict[str, str]:
    """Map login names -> broadcaster ids (Helix accepts up to 100 at once)."""
    out: dict[str, str] = {}
    for i in range(0, len(logins), 100):
        batch = logins[i : i + 100]
        data = _get("/users", params=[("login", x) for x in batch])
        for u in data.get("data", []):
            out[u["login"].lower()] = u["id"]
    return out


def top_clips(broadcaster_id: str, lookback_hours: int, first: int = 20) -> list[dict]:
    """Most-viewed clips for a broadcaster within the lookback window."""
    started = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    ended = datetime.now(timezone.utc).isoformat()
    data = _get("/clips", params={
        "broadcaster_id": broadcaster_id,
        "first": min(first, 100),
        "started_at": started,
        "ended_at": ended,
    })
    clips = data.get("data", [])
    clips.sort(key=lambda c: c.get("view_count", 0), reverse=True)
    return clips
