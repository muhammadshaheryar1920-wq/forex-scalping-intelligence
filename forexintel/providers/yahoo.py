"""Yahoo Finance chart endpoint (free, no key). MID prices only: spreads are ASSUMED, never real.

Spot gold is not available (XAUUSD=X returns 404 and GC=F is a futures proxy), so XAUUSD is unsupported.
Unofficial endpoint: treat as indicative, best-effort data for EUR/USD and GBP/USD only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from .base import MarketDataProvider, ProviderError, empty_frame, from_mid, http_get

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (forexintel research)"}


def parse_chart(payload: dict, symbol: str, now: datetime | None = None) -> pd.DataFrame:
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ProviderError(f"yahoo error: {chart['error']}")
    res = chart.get("result") or []
    if not res or not res[0].get("timestamp"):
        return empty_frame()
    r = res[0]
    q = r["indicators"]["quote"][0]
    idx = pd.to_datetime(r["timestamp"], unit="s", utc=True)
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q.get("volume") or [0] * len(idx)}, index=idx)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)
    return from_mid(df, symbol, now=now)


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    verified = True          # EURUSD/GBPUSD M5 verified Oct 2026
    has_bid_ask = False

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe != "M5":
            raise ProviderError("yahoo adapter provides M5 only")
        sym = self.provider_symbol(symbol)
        floor = datetime.now(timezone.utc) - timedelta(days=58)      # Yahoo intraday history limit
        start = max(start, floor)
        r = http_get(URL.format(sym=sym), params={
            "interval": "5m", "period1": int(start.timestamp()), "period2": int(end.timestamp()) + 300,
            "includePrePost": "false"}, headers=HEADERS)
        return parse_chart(r.json(), symbol)

    def latest_quote(self, symbol: str) -> dict | None:
        return None   # mid-only source: no honest bid/ask

    def health(self) -> dict:
        try:
            r = http_get(URL.format(sym="EURUSD=X"), params={"interval": "5m", "range": "1d"}, headers=HEADERS, tries=2)
            return {"provider": self.name, "ok": r.status_code == 200, "note": "mid-only, spread assumed, EURUSD/GBPUSD only"}
        except ProviderError as e:
            return {"provider": self.name, "ok": False, "detail": str(e)}
