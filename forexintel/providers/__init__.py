from __future__ import annotations

from .base import MarketDataProvider, ProviderBlocked, ProviderError


def get_provider(name: str) -> MarketDataProvider:
    name = name.lower()
    if name == "dukascopy":
        from .dukascopy import DukascopyProvider
        return DukascopyProvider()
    if name == "yahoo":
        from .yahoo import YahooProvider
        return YahooProvider()
    if name == "oanda":
        from .oanda import OandaProvider
        return OandaProvider()
    if name == "twelvedata":
        from .twelvedata import TwelveDataProvider
        return TwelveDataProvider()
    if name == "mt5":
        from .mt5 import MT5Provider
        return MT5Provider()
    if name == "demo":
        from .demo import DemoProvider
        return DemoProvider()
    raise ProviderError(f"unknown provider '{name}' (dukascopy|yahoo|oanda|twelvedata|mt5|demo)")


PROVIDER_NAMES = ("dukascopy", "yahoo", "oanda", "twelvedata", "mt5", "demo")

__all__ = ["get_provider", "PROVIDER_NAMES", "MarketDataProvider", "ProviderBlocked", "ProviderError"]
