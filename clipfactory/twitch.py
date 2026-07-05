"""Fetch top clips from Twitch via the Helix API (App Access Token / client-credentials)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from .config import env

HELIX = "https://api.twitch.tv/helix"
_token_cache: dict = {"token": None, "exp": 0}


def _app_token() -> str:
    cid, secret = env("TWITCH_CLIENT_ID"), env("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env")
    if _token_cache["token"] and _token_cache["exp"] > time.time() + 60:
        return _token_cache["token"]
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
    data = r.json()
    _token_cache.update(token=data["access_token"], exp=time.time() + data["expires_in"])
    return data["access_token"]


def _headers() -> dict:
    return {"Client-Id": env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {_app_token()}"}


def resolve_user_ids(logins: list[str]) -> dict[str, str]:
    """Map login names -> broadcaster ids (Helix accepts up to 100 at once)."""
    out: dict[str, str] = {}
    for i in range(0, len(logins), 100):
        batch = logins[i : i + 100]
        r = requests.get(
            f"{HELIX}/users",
            headers=_headers(),
            params=[("login", x) for x in batch],
            timeout=30,
        )
        r.raise_for_status()
        for u in r.json().get("data", []):
            out[u["login"].lower()] = u["id"]
    return out


def top_clips(broadcaster_id: str, lookback_hours: int, first: int = 20) -> list[dict]:
    """Most-viewed clips for a broadcaster within the lookback window."""
    started = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    ended = datetime.now(timezone.utc).isoformat()
    r = requests.get(
        f"{HELIX}/clips",
        headers=_headers(),
        params={
            "broadcaster_id": broadcaster_id,
            "first": min(first, 100),
            "started_at": started,
            "ended_at": ended,
        },
        timeout=30,
    )
    r.raise_for_status()
    clips = r.json().get("data", [])
    clips.sort(key=lambda c: c.get("view_count", 0), reverse=True)
    return clips
