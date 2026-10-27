"""Candle persistence, resampling and composed (history + live tail) series loading."""
from __future__ import annotations

import hashlib
import time

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .. import db
from ..config import TF_SECONDS, get_settings
from ..providers.base import BA_COLS, FRAME_COLS, MID_COLS

DB_COLS = ["open", "high", "low", "close", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "volume"]


def _row_hash(r) -> str:
    return hashlib.sha1("|".join(f"{v:.6f}" for v in r).encode()).hexdigest()[:20]


def save_candles(df: pd.DataFrame, provider: str, symbol: str, tf: str = "M5", only_complete: bool = True) -> int:
    """Idempotent upsert of canonical frame rows. Only CLOSED bars are persisted by default."""
    if df is None or df.empty:
        return 0
    if only_complete:
        df = df[df["is_complete"]]
    if df.empty:
        return 0
    now = int(time.time())
    ts = (df.index.astype("int64") // 10**9).astype(int)
    vals = df[DB_COLS].to_numpy(dtype=float)
    hb = df["has_bid_ask"].to_numpy(dtype=bool)
    rows = []
    for k in range(len(df)):
        d = dict(zip(DB_COLS, (float(x) for x in vals[k])))
        rows.append(dict(provider=provider, symbol=symbol, timeframe=tf, open_ts=int(ts[k]),
                         close_ts=int(ts[k]) + TF_SECONDS[tf], n_src=1, is_complete=True,
                         has_bid_ask=bool(hb[k]), source_hash=_row_hash(vals[k][:8]), received_ts=now, **d))
    with db.begin() as c:
        db.upsert(c, db.candles, rows, ["provider", "symbol", "timeframe", "open_ts"])
    return len(rows)


def load_raw(symbol: str, provider: str, tf: str = "M5", start=None, end=None) -> pd.DataFrame:
    c = db.candles
    q = select(c.c.open_ts, *[c.c[k] for k in DB_COLS], c.c.has_bid_ask).where(
        c.c.symbol == symbol, c.c.provider == provider, c.c.timeframe == tf, c.c.is_complete.is_(True))
    if start is not None:
        q = q.where(c.c.open_ts >= int(pd.Timestamp(start).timestamp()))
    if end is not None:
        q = q.where(c.c.open_ts <= int(pd.Timestamp(end).timestamp()))
    q = q.order_by(c.c.open_ts)
    with db.get_engine().connect() as conn:
        df = pd.DataFrame(conn.execute(q).fetchall(), columns=["ts"] + DB_COLS + ["has_bid_ask"])
    if df.empty:
        from ..providers.base import empty_frame
        return empty_frame()
    df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True)
    df.index.name = "ts"
    df["has_bid_ask"] = df["has_bid_ask"].astype(bool)
    df["is_complete"] = True
    return df[FRAME_COLS]


def last_complete_ts(provider: str, symbol: str, tf: str = "M5"):
    c = db.candles
    with db.get_engine().connect() as conn:
        v = conn.execute(select(func.max(c.c.open_ts)).where(
            c.c.provider == provider, c.c.symbol == symbol, c.c.timeframe == tf, c.c.is_complete.is_(True))).scalar()
    return None if v is None else pd.Timestamp(v, unit="s", tz="UTC")


def first_complete_ts(provider: str, symbol: str, tf: str = "M5"):
    c = db.candles
    with db.get_engine().connect() as conn:
        v = conn.execute(select(func.min(c.c.open_ts)).where(
            c.c.provider == provider, c.c.symbol == symbol, c.c.timeframe == tf, c.c.is_complete.is_(True))).scalar()
    return None if v is None else pd.Timestamp(v, unit="s", tz="UTC")


def resample(m5: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Aggregate CLOSED M5 bars into M15/H1. A bucket is emitted only once its end time has passed
    (end <= close of the last available M5 bar), so a forming higher-timeframe bar can never leak."""
    if tf == "M5" or m5.empty:
        return m5
    step = f"{TF_SECONDS[tf]}s"
    g = m5.resample(step, label="left", closed="left")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "bo": "first", "bh": "max", "bl": "min",
           "bc": "last", "ao": "first", "ah": "max", "al": "min", "ac": "last", "volume": "sum"}
    out = g.agg(agg)
    out["n_src"] = g["close"].count()
    out = out[out["n_src"] > 0].copy()
    out["has_bid_ask"] = g["has_bid_ask"].min().reindex(out.index).astype(bool)
    last_close = m5.index[-1] + pd.Timedelta(seconds=TF_SECONDS["M5"])
    ends = out.index + pd.Timedelta(seconds=TF_SECONDS[tf])
    out = out[ends <= last_close]
    out["is_complete"] = True
    out.index.name = "ts"
    return out


_series_cache: dict = {}


def _stamp(provider: str, symbol: str) -> tuple:
    c = db.candles
    with db.get_engine().connect() as conn:
        r = conn.execute(select(func.max(c.c.open_ts), func.count()).where(
            c.c.provider == provider, c.c.symbol == symbol, c.c.timeframe == "M5")).first()
    return (r[0], r[1])


def load_series(symbol: str, history_provider: str | None = None, live_provider: str | None = None,
                start=None, end=None) -> tuple[pd.DataFrame, dict]:
    """Composed M5 series: history provider bars, then live-provider bars strictly AFTER the last history bar.

    Providers whose series is a proxy (spot_compatible False) or that lack the symbol are never stitched.
    Returns (frame, meta). meta records every source so output can show data lineage honestly.
    """
    from ..providers import get_provider
    s = get_settings()
    hp = history_provider or s.history_provider
    lp = live_provider if live_provider is not None else s.live_provider
    ckey = None
    if start is None and end is None:
        ckey = (symbol, hp, lp, _stamp(hp, symbol), _stamp(lp, symbol) if lp not in (None, "", "none") else None, db.get_engine().url.render_as_string())
        if ckey in _series_cache:
            fr, mt = _series_cache[ckey]
            return fr, dict(mt)
    hist = load_raw(symbol, hp, "M5", start, end)
    meta = {"history_provider": hp, "live_provider": None, "stitched": False, "history_end": None,
            "tail_bars": 0, "assumed_spread_bars": int((~hist["has_bid_ask"]).sum()) if len(hist) else 0}
    frame = hist
    if len(hist):
        meta["history_end"] = hist.index[-1].isoformat()
    if lp and lp not in ("none", hp):
        try:
            prov = get_provider(lp)
            ok = prov.supports(symbol) and prov.spot_compatible
        except Exception:
            ok = False
        if ok:
            tail = load_raw(symbol, lp, "M5", start, end)
            if len(hist):
                tail = tail[tail.index > hist.index[-1]]
            if len(tail):
                frame = pd.concat([hist, tail]) if len(hist) else tail
                meta.update(live_provider=lp, stitched=bool(len(hist)), tail_bars=int(len(tail)))
                if len(hist):
                    meta["seam_jump"] = float(tail["open"].iloc[0] - hist["close"].iloc[-1])
                meta["assumed_spread_bars"] = int((~frame["has_bid_ask"]).sum())
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    if ckey is not None:
        if len(_series_cache) >= 6:
            _series_cache.pop(next(iter(_series_cache)))
        _series_cache[ckey] = (frame, dict(meta))
    return frame, meta
