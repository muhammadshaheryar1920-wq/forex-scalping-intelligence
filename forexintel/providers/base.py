"""Provider adapter contract and normalized candle frame helpers.

Canonical frame (M5 base): index = bar OPEN time (UTC tz-aware, name "ts"); columns
bo,bh,bl,bc (bid OHLC), ao,ah,al,ac (ask OHLC), open,high,low,close (mid), volume,
has_bid_ask (bool: real bid/ask?), is_complete (bool).
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from ..config import INSTRUMENTS, TF_SECONDS

BA_COLS = ["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]
MID_COLS = ["open", "high", "low", "close"]
FRAME_COLS = BA_COLS + MID_COLS + ["volume", "has_bid_ask", "is_complete"]


class ProviderError(Exception):
    pass


class ProviderBlocked(ProviderError):
    """Provider unusable (missing credentials / package). Never fall back to fake data."""


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="ts")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in BA_COLS + MID_COLS + ["volume"]}
                        | {"has_bid_ask": pd.Series(dtype="bool"), "is_complete": pd.Series(dtype="bool")}).set_axis(idx)


def mark_complete(idx: pd.DatetimeIndex, tf: str = "M5", now: datetime | None = None, lag_s: int = 2) -> np.ndarray:
    now = now or datetime.now(timezone.utc)
    close = idx + pd.Timedelta(seconds=TF_SECONDS[tf])
    return np.asarray(close <= pd.Timestamp(now) - pd.Timedelta(seconds=lag_s))


def from_mid(df: pd.DataFrame, symbol: str, now: datetime | None = None) -> pd.DataFrame:
    """Mid-only source -> canonical frame with ASSUMED symmetric spread (has_bid_ask=False)."""
    half = INSTRUMENTS[symbol].assumed_spread / 2.0
    out = pd.DataFrame(index=df.index)
    for src, b, a in (("open", "bo", "ao"), ("high", "bh", "ah"), ("low", "bl", "al"), ("close", "bc", "ac")):
        out[b] = df[src] - half
        out[a] = df[src] + half
    for c in MID_COLS:
        out[c] = df[c]
    out["volume"] = df["volume"] if "volume" in df else 0.0
    out["has_bid_ask"] = False
    out["is_complete"] = mark_complete(out.index, now=now)
    return _finish(out)


def from_bid_ask(bid: pd.DataFrame, ask: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """bid/ask frames each with open/high/low/close(/volume) -> canonical frame."""
    idx = bid.index.intersection(ask.index)
    b, a = bid.loc[idx], ask.loc[idx]
    out = pd.DataFrame(index=idx)
    for k, bc_, ac_ in (("open", "bo", "ao"), ("high", "bh", "ah"), ("low", "bl", "al"), ("close", "bc", "ac")):
        out[bc_] = b[k].to_numpy()
        out[ac_] = a[k].to_numpy()
        out[k] = (b[k].to_numpy() + a[k].to_numpy()) / 2.0
    out["volume"] = b["volume"].to_numpy() if "volume" in b else 0.0
    out["has_bid_ask"] = True
    out["is_complete"] = mark_complete(out.index, now=now)
    return _finish(out)


def _finish(out: pd.DataFrame) -> pd.DataFrame:
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "ts"
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out[FRAME_COLS]


def http_get(url: str, params: dict | None = None, headers: dict | None = None, tries: int = 4,
             timeout: float = 30.0, ok_statuses: tuple = (200,), session: requests.Session | None = None):
    """GET with bounded exponential backoff + jitter. Returns Response (status may be in ok_statuses only)."""
    s = session or requests
    last: Exception | None = None
    for n in range(tries):
        try:
            r = s.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in ok_statuses:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                last = ProviderError(f"HTTP {r.status_code} from {url.split('?')[0]}")
            else:
                raise ProviderError(f"HTTP {r.status_code} from {url.split('?')[0]}: {r.text[:200]}")
        except requests.RequestException as e:
            last = ProviderError(f"network error: {e.__class__.__name__}")
        time.sleep(min(30.0, (2 ** n) + random.random()))
    raise last or ProviderError("request failed")


class MarketDataProvider(ABC):
    name = "base"
    verified = False          # True only when proven against the live service by this project
    has_bid_ask = False
    streaming = False
    spot_compatible = True    # False => series is a proxy (e.g. futures) and must not be stitched with spot history

    def supports(self, symbol: str) -> bool:
        return INSTRUMENTS[symbol].provider_symbols.get(self.name) is not None

    def provider_symbol(self, symbol: str) -> str:
        ps = INSTRUMENTS[symbol].provider_symbols.get(self.name)
        if ps is None:
            raise ProviderError(f"{self.name} does not provide {symbol}")
        return ps

    def list_instruments(self) -> list[str]:
        return [s for s in INSTRUMENTS if self.supports(s)]

    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame: ...

    def latest_quote(self, symbol: str) -> dict | None:
        return None

    def subscribe_quotes(self, symbols):  # optional capability
        raise NotImplementedError(f"{self.name} has no streaming adapter; the monitor polls on candle close")

    def get_spread(self, symbol: str) -> float | None:
        q = self.latest_quote(symbol)
        return None if q is None else q.get("spread")

    def health(self) -> dict:
        return {"provider": self.name, "ok": True, "detail": "no active check implemented"}

    def capabilities(self) -> dict:
        return {
            "provider": self.name, "bid_ask": self.has_bid_ask, "streaming": self.streaming,
            "symbols": {s: self.supports(s) for s in INSTRUMENTS},
            "timeframes": ["M5"], "verified": self.verified, "spot_compatible": self.spot_compatible,
        }
