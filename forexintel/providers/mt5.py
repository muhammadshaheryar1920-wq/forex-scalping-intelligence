"""MetaTrader 5 adapter (Windows only; needs a broker terminal installed and logged in).

UNVERIFIED: written against MetaTrader5 Python docs (copy_rates_range returns bid-based bars incl. spread in points);
no terminal was available to test. Symbol names differ per broker (e.g. XAUUSD.m): override via MT5_SYMBOL_<SYM>.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

from .base import MarketDataProvider, ProviderBlocked, ProviderError, empty_frame, from_bid_ask


class MT5Provider(MarketDataProvider):
    name = "mt5"
    verified = False
    has_bid_ask = True        # bid OHLC + spread (points) => ask derived from the bar's spread

    def _mt5(self):
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as e:
            raise ProviderBlocked("MetaTrader5 package not installed (pip install MetaTrader5; Windows only)") from e
        if not mt5.initialize():
            raise ProviderBlocked(f"MT5 terminal not available: {mt5.last_error()}")
        return mt5

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe != "M5":
            raise ProviderError("mt5 adapter fetches M5 as the base timeframe")
        mt5 = self._mt5()
        sym = os.getenv(f"MT5_SYMBOL_{symbol}", self.provider_symbol(symbol))
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        info = mt5.symbol_info(sym)
        if rates is None or len(rates) == 0 or info is None:
            return empty_frame()
        df = pd.DataFrame(rates)
        idx = pd.to_datetime(df["time"], unit="s", utc=True)
        bid = pd.DataFrame({k: df[k].to_numpy() for k in ("open", "high", "low", "close")}, index=idx)
        bid["volume"] = df["tick_volume"].to_numpy()
        ask = bid.copy()
        off = df["spread"].to_numpy() * info.point
        for k in ("open", "high", "low", "close"):
            ask[k] = ask[k] + off
        return from_bid_ask(bid, ask)

    def health(self) -> dict:
        try:
            self._mt5()
            return {"provider": self.name, "ok": True}
        except ProviderBlocked as e:
            return {"provider": self.name, "ok": False, "blocked": True, "detail": str(e)}
