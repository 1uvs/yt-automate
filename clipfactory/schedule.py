"""Compute upcoming peak-time posting slots for scheduled publishing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_SLOTS = ["12:00", "15:00", "18:00", "21:00"]


def _tz(cfg: dict):
    name = (cfg.get("schedule") or {}).get("timezone", "auto")
    if name and name != "auto":
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo  # system local timezone


def next_slots(n: int, cfg: dict, now: datetime | None = None) -> list[datetime]:
    """Return the next n posting datetimes (tz-aware), one per configured slot."""
    sch = cfg.get("schedule") or {}
    slots = sch.get("slots") or DEFAULT_SLOTS
    times = sorted(tuple(int(x) for x in s.split(":")) for s in slots)

    tz = _tz(cfg)
    now = now or datetime.now(tz)

    out: list[datetime] = []
    day = 0
    while len(out) < n:
        base = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        for hh, mm in times:
            slot = base.replace(hour=hh, minute=mm)
            if slot > now + timedelta(minutes=2):  # small buffer; must be in the future
                out.append(slot)
                if len(out) >= n:
                    break
        day += 1
    return out


def to_publish_at(dt: datetime) -> str:
    """RFC3339 UTC string that YouTube's publishAt expects."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def human(dt: datetime) -> str:
    return dt.strftime("%a %b %d · %-I:%M %p")
