"""Data quality / freshness assessment. Hard gate: no actionable signal unless status is HEALTHY."""
from __future__ import annotations

import pandas as pd

from ..config import TF_SECONDS, get_settings
from .calendar import expected_index, find_gaps, is_market_open

MIN_BARS = 2700          # ~9 trading days of M5: enough warm-up for H1 EMA200 and rolling ranks
FIVE = pd.Timedelta(seconds=TF_SECONDS["M5"])


def assess(symbol: str, m5: pd.DataFrame, now=None) -> dict:
    s = get_settings()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    out = {"status": "NO_DATA", "bars": int(len(m5)), "last_bar_open": None, "age_minutes": None,
           "lag_bars": None, "recent_missing_bars": None, "market_open": is_market_open(symbol, now), "notes": []}
    if m5.empty:
        out["notes"].append("no candles stored: run `python main.py sync`")
        return out
    last = m5.index[-1]
    out["last_bar_open"] = last.isoformat()
    out["age_minutes"] = round((now - (last + FIVE)).total_seconds() / 60.0, 1)
    exp_tail = expected_index(symbol, now - pd.Timedelta(days=4), now - FIVE)
    exp_last = exp_tail[-1] if len(exp_tail) else last
    lag = int((exp_tail > last).sum()) if len(exp_tail) else 0
    out["lag_bars"] = lag
    window_start = m5.index[max(0, len(m5) - 200)]
    gaps = find_gaps(symbol, m5.index, window_start, last)
    out["recent_missing_bars"] = int(sum(g["bars"] for g in gaps))
    out["recent_gaps"] = gaps[:5]
    if len(m5) < MIN_BARS:
        out["status"] = "INSUFFICIENT_DATA"
        out["notes"].append(f"{len(m5)} bars < {MIN_BARS} required for stable M15/H1 context")
    elif lag > s.stale_after_bars:
        out["status"] = "STALE"
        out["notes"].append(f"last complete M5 bar is {lag} expected bars behind (age {out['age_minutes']} min)")
    elif out["recent_missing_bars"] > s.max_recent_gap_bars:
        out["status"] = "DEGRADED"
        out["notes"].append(f"{out['recent_missing_bars']} M5 bars missing in the last 200")
    elif not out["market_open"] and last >= exp_last:
        out["status"] = "MARKET_CLOSED"
    else:
        out["status"] = "HEALTHY"
    return out
