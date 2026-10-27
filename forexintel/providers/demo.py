"""Deterministic SYNTHETIC data (regime-switching random walk). Always labelled DEMO.

Used for tests and for exercising the pipeline offline. Never presented as market data and never
authorizes trades (the engine forces NO_TRADE for the demo provider).
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import INSTRUMENTS
from .base import MarketDataProvider, from_bid_ask
from ..data.calendar import open_mask

START_PRICE = {"XAUUSD": 2650.0, "EURUSD": 1.0850, "GBPUSD": 1.2900}
DEMO_BASE = pd.Timestamp("2026-01-05", tz="UTC")


@lru_cache(maxsize=8)
def _series(symbol: str, n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    ins = INSTRUMENTS[symbol]
    p0 = START_PRICE[symbol]
    daily_vol = (0.0075 if symbol == "XAUUSD" else 0.0045)
    sig_base = p0 * daily_vol / np.sqrt(288)
    # regimes: 0 range, 1 up-trend, 2 down-trend, 3 high-vol
    drift = np.zeros(n_bars)
    sig = np.full(n_bars, sig_base)
    mean_rev = np.zeros(n_bars)
    i = 0
    while i < n_bars:
        L = int(rng.integers(150, 900))
        reg = int(rng.integers(0, 4))
        sl = slice(i, min(n_bars, i + L))
        if reg == 1:
            drift[sl] = sig_base * 0.12
        elif reg == 2:
            drift[sl] = -sig_base * 0.12
        elif reg == 3:
            sig[sl] = sig_base * 2.2
        else:
            mean_rev[sl] = 0.05
        i += L
    close = np.empty(n_bars)
    anchor = p0
    x = p0
    for t in range(n_bars):
        if mean_rev[t] > 0:
            x += mean_rev[t] * (anchor - x) + rng.normal(0, sig[t])
        else:
            x += drift[t] + rng.normal(0, sig[t])
            anchor = x
        close[t] = x
    idx = pd.date_range(DEMO_BASE, periods=n_bars, freq="5min", tz="UTC")
    open_ = np.concatenate([[p0], close[:-1]])
    wick = np.abs(rng.normal(0, sig_base * 0.6, n_bars))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick * rng.uniform(0.5, 1.5, n_bars)
    spread = ins.assumed_spread * rng.uniform(0.7, 1.6, n_bars)
    mid = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 100.0}, index=idx)
    mask = open_mask(symbol, idx)
    mid = mid[mask]
    sp = pd.Series(spread, index=idx)[mask].to_numpy() / 2
    bid, ask = mid.copy(), mid.copy()
    for k in ("open", "high", "low", "close"):
        bid[k] = mid[k] - sp
        ask[k] = mid[k] + sp
    return from_bid_ask(bid, ask, now=datetime(2100, 1, 1, tzinfo=timezone.utc))


class DemoProvider(MarketDataProvider):
    name = "demo"
    verified = False
    has_bid_ask = True
    spot_compatible = False

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        now = pd.Timestamp.now(tz="UTC")
        n = int((now - DEMO_BASE).total_seconds() // 300) + 1
        full = _series(symbol, n).copy()
        full["is_complete"] = (full.index + pd.Timedelta(minutes=5)) <= now
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        return full[(full.index >= lo) & (full.index <= hi)]

    def health(self) -> dict:
        return {"provider": self.name, "ok": True, "note": "SYNTHETIC DEMO DATA - not market data"}
