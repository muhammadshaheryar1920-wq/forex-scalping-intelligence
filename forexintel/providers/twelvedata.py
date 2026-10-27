"""Twelve Data time_series adapter (free key: 8 credits/min, 800/day). MID prices only; spread assumed.

XAU/USD availability on the free Basic plan is NOT confirmed by their docs; the adapter reports the API error
verbatim if the plan does not include it. Not verified end-to-end (no key available to this project).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..config import get_settings
from .base import MarketDataProvider, ProviderBlocked, ProviderError, empty_frame, from_mid, http_get

URL = "https://api.twelvedata.com/time_series"


def parse_series(payload: dict, symbol: str) -> pd.DataFrame:
    if payload.get("status") == "error":
        raise ProviderError(f"twelvedata error {payload.get('code')}: {payload.get('message')}")
    vals = payload.get("values") or []
    if not vals:
        return empty_frame()
    df = pd.DataFrame(vals)
    idx = pd.to_datetime(df["datetime"], utc=True)
    out = pd.DataFrame({c: pd.to_numeric(df[c]) for c in ("open", "high", "low", "close")})
    out["volume"] = pd.to_numeric(df["volume"]) if "volume" in df else 0.0
    out.index = idx
    return from_mid(out.sort_index(), symbol)


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    verified = False
    has_bid_ask = False

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe != "M5":
            raise ProviderError("twelvedata adapter fetches M5 as the base timeframe")
        key = get_settings().twelvedata_key
        if not key:
            raise ProviderBlocked("TWELVEDATA_API_KEY not set (free key at twelvedata.com)")
        sym = self.provider_symbol(symbol)
        frames, cur_end = [], end
        for _ in range(40):                                  # each page <= 5000 bars (~17 days)
            r = http_get(URL, params={
                "symbol": sym, "interval": "5min", "outputsize": 5000, "timezone": "UTC", "order": "desc",
                "end_date": cur_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "start_date": start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "apikey": key})
            page = parse_series(r.json(), symbol)
            if page.empty:
                break
            frames.append(page)
            oldest = page.index.min().to_pydatetime()
            if oldest <= start or len(page) < 5000:
                break
            cur_end = oldest - timedelta(minutes=5)
            time.sleep(8)                                    # stay under 8 credits/minute
        return pd.concat(frames).sort_index() if frames else empty_frame()

    def health(self) -> dict:
        if not get_settings().twelvedata_key:
            return {"provider": self.name, "ok": False, "blocked": True, "detail": "TWELVEDATA_API_KEY not set"}
        return {"provider": self.name, "ok": True, "note": "key present; run doctor --deep to test a request"}
