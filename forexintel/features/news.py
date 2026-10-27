"""Economic-event filter. Free source: Forex Factory weekly JSON mirror (unofficial; terms unverified).

Point-in-time honesty: events are stored with `published_ts` = when WE fetched them, so history accumulates
going forward; no past week is reconstructed. Backtests therefore run WITHOUT a news filter (documented).
If the calendar cannot be fetched, status is UNAVAILABLE and policy (warn|block) applies; nothing is invented.
"""
from __future__ import annotations

import hashlib
import time

import pandas as pd
from sqlalchemy import select

from .. import db
from ..config import INSTRUMENTS, get_settings
from ..providers.base import ProviderError, http_get

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STALE_AFTER_H = 36


def parse_events(payload: list[dict], fetched_ts: int | None = None) -> list[dict]:
    fetched_ts = fetched_ts or int(time.time())
    out = []
    for e in payload:
        try:
            ts = int(pd.Timestamp(e["date"]).tz_convert("UTC").timestamp())
        except Exception:  # noqa: BLE001
            continue
        cur = str(e.get("country", "")).upper()
        title = str(e.get("title", ""))
        out.append(dict(event_id=hashlib.sha1(f"{cur}|{title}|{ts}".encode()).hexdigest()[:24], currency=cur, title=title[:200],
                        scheduled_ts=ts, impact=str(e.get("impact", "")).title(), forecast=str(e.get("forecast", "") or ""),
                        previous=str(e.get("previous", "") or ""), actual=str(e.get("actual", "") or ""),
                        published_ts=fetched_ts, source="faireconomy-ff-weekly"))
    return out


def refresh_calendar() -> dict:
    db.init_db()
    try:
        r = http_get(FF_URL, headers={"User-Agent": "Mozilla/5.0 (forexintel research)"}, tries=3, timeout=20)
        events = parse_events(r.json())
    except (ProviderError, ValueError) as e:
        return {"ok": False, "error": str(e), "events": 0}
    with db.begin() as c:
        db.upsert(c, db.economic_events, events, ["event_id"])
    return {"ok": True, "events": len(events)}


def calendar_state(now=None) -> dict:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    with db.get_engine().connect() as c:
        last = c.execute(select(db.economic_events.c.published_ts).order_by(db.economic_events.c.published_ts.desc()).limit(1)).scalar()
    if last is None:
        return {"status": "CALENDAR_UNAVAILABLE", "last_fetch": None}
    age_h = (now.timestamp() - last) / 3600
    return {"status": "CALENDAR_UNAVAILABLE" if age_h > STALE_AFTER_H else "OK", "last_fetch_age_h": round(age_h, 1)}


def upcoming(symbol: str, now=None, hours: int = 48, impacts=("High", "Medium")) -> list[dict]:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    cur = INSTRUMENTS[symbol].news_currencies
    e = db.economic_events
    q = select(e).where(e.c.scheduled_ts >= int(now.timestamp()) - 3600, e.c.scheduled_ts <= int(now.timestamp()) + hours * 3600,
                        e.c.currency.in_(cur), e.c.impact.in_(impacts)).order_by(e.c.scheduled_ts)
    with db.get_engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
    for r in rows:
        r["minutes_to"] = round((r["scheduled_ts"] - now.timestamp()) / 60, 1)
        r["scheduled_utc"] = pd.Timestamp(r["scheduled_ts"], unit="s", tz="UTC").isoformat()
    return rows


def blackout(symbol: str, now=None) -> dict:
    s = get_settings()
    state = calendar_state(now)
    if state["status"] != "OK":
        return {"state": "UNAVAILABLE", "calendar": state, "events": []}
    ev = [x for x in upcoming(symbol, now, hours=2, impacts=("High",))
          if -s.news_post_minutes <= x["minutes_to"] <= s.news_pre_minutes]
    return {"state": "BLACKOUT" if ev else "CLEAR", "calendar": state, "events": ev}
