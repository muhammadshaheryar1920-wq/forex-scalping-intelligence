"""Catch-up synchronization: find last saved candle, fetch the missing span, validate, persist checkpoint."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .. import db
from ..config import SYMBOLS, TF_SECONDS, get_settings, normalize_symbol
from ..providers import ProviderBlocked, ProviderError, get_provider
from . import store
from .calendar import find_gaps

DEFAULT_DAYS = {"dukascopy": 90, "yahoo": 58, "oanda": 90, "twelvedata": 60, "mt5": 90, "demo": 120}
OVERLAP = timedelta(hours=2)          # re-fetch the tail to absorb provider revisions (upserts are idempotent)


@dataclass
class SyncReport:
    symbol: str
    provider: str
    status: str = "OK"                 # OK | BLOCKED | UNSUPPORTED | FAILED
    requested_from: str | None = None
    requested_to: str | None = None
    fetched: int = 0
    stored_total: int = 0
    first_bar: str | None = None
    last_bar: str | None = None
    freshness_minutes: float | None = None
    gaps: list = field(default_factory=list)
    failed_days: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _floor5(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0, minute=t.minute - t.minute % 5)


def _record_state(provider: str, symbol: str, rep: SyncReport) -> None:
    last = store.last_complete_ts(provider, symbol)
    with db.begin() as c:
        db.upsert(c, db.sync_state, [dict(
            provider=provider, symbol=symbol, timeframe="M5",
            last_complete_open_ts=None if last is None else int(last.timestamp()),
            last_sync_ts=int(time.time()), status=rep.status,
            detail={"gaps": rep.gaps[:20], "failed_days": rep.failed_days, "error": rep.error, "notes": rep.notes})],
            ["provider", "symbol", "timeframe"])


def sync_symbol(symbol: str, provider_name: str | None = None, days: int | None = None, catch_up: bool = True,
                now: datetime | None = None, tail_only: bool = False) -> SyncReport:
    symbol = normalize_symbol(symbol)
    pname = provider_name or get_settings().history_provider
    rep = SyncReport(symbol=symbol, provider=pname)
    db.init_db()
    try:
        prov = get_provider(pname)
        if not prov.supports(symbol):
            rep.status = "UNSUPPORTED"
            rep.notes.append(f"{pname} does not provide {symbol}")
            _record_state(pname, symbol, rep)
            return rep
        now = now or datetime.now(timezone.utc)
        end = _floor5(now)
        last = store.last_complete_ts(pname, symbol)
        n_days = days or DEFAULT_DAYS.get(pname, 60)
        if last is None:
            start = end - timedelta(days=n_days)
            if tail_only:
                start = end - timedelta(days=4)
            rep.notes.append(f"first sync: back-filling {n_days if not tail_only else 4} days")
        else:
            start = last.to_pydatetime() - OVERLAP
            have = store.load_raw(symbol, pname, start=end - timedelta(days=n_days))
            if len(have):
                gaps = find_gaps(symbol, have.index, have.index[0], have.index[-1])
                reopen = [pd.Timestamp(g["start"]) for g in gaps if g["bars"] >= 2]
                if reopen:
                    start = min(start, min(reopen).to_pydatetime() - timedelta(hours=1))
                    rep.notes.append(f"re-requesting {len(reopen)} previously unfilled gap(s)")
            first = store.first_complete_ts(pname, symbol)
            want_start = end - timedelta(days=days) if days else None
            if want_start is not None and first is not None and (first.to_pydatetime() - want_start) > timedelta(days=2)                     and not tail_only:
                start = want_start
                rep.notes.append(f"deepening history back to {want_start:%Y-%m-%d} (cached day files are not re-downloaded)")
            gap_days = (end - last.to_pydatetime()).total_seconds() / 86400
            if gap_days > 1.5:
                rep.notes.append(f"catch-up after {gap_days:.1f} days offline")
        rep.requested_from, rep.requested_to = start.isoformat(), end.isoformat()
        frame = prov.fetch_candles(symbol, "M5", start, end)
        rep.failed_days = getattr(prov, "last_report", {}).get("failed_days", [])
        rep.fetched = int(len(frame))
        store.save_candles(frame, pname, symbol)
    except ProviderBlocked as e:
        rep.status, rep.error = "BLOCKED", str(e)
        _record_state(pname, symbol, rep)
        return rep
    except ProviderError as e:
        rep.status, rep.error = "FAILED", str(e)
        _record_state(pname, symbol, rep)
        return rep

    allbars = store.load_raw(symbol, pname)
    rep.stored_total = int(len(allbars))
    if len(allbars):
        rep.first_bar, rep.last_bar = allbars.index[0].isoformat(), allbars.index[-1].isoformat()
        rep.freshness_minutes = round((pd.Timestamp(now) - (allbars.index[-1] + pd.Timedelta(minutes=5))).total_seconds() / 60, 1)
        win_start = max(allbars.index[0], pd.Timestamp(end) - pd.Timedelta(days=n_days))
        rep.gaps = find_gaps(symbol, allbars.index, win_start, allbars.index[-1])
    if rep.failed_days:
        rep.notes.append(f"{len(rep.failed_days)} day file(s) failed to download; will be retried on next sync")
    _record_state(pname, symbol, rep)
    db.audit("sync", {"symbol": symbol, "provider": pname, "fetched": rep.fetched, "gaps": len(rep.gaps)})
    return rep


def sync_composed(symbol: str, days: int | None = None, now: datetime | None = None,
                  history: bool = True, live: bool = True) -> list[SyncReport]:
    """Sync the history provider and (if configured/compatible) the live-tail provider."""
    s = get_settings()
    reports = []
    if history:
        reports.append(sync_symbol(symbol, s.history_provider, days=days, now=now))
    lp = s.live_provider
    if live and lp not in ("none", "", s.history_provider):
        reports.append(sync_symbol(symbol, lp, days=days, now=now, tail_only=True))
    return reports


def sync_many(symbols=None, **kw) -> list[SyncReport]:
    out = []
    for sym in symbols or SYMBOLS:
        out.extend(sync_composed(sym, **kw))
    return out
