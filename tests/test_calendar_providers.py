import lzma
import struct
from datetime import datetime, timezone

import pandas as pd
import pytest

from forexintel.config import INSTRUMENTS
from forexintel.data.calendar import expected_index, find_gaps, is_market_open
from forexintel.providers import ProviderBlocked, ProviderError, get_provider
from forexintel.providers import base as pbase
from forexintel.providers.dukascopy import parse_bi5, to_m5
from forexintel.providers.oanda import OandaProvider, parse_candles
from forexintel.providers.twelvedata import TwelveDataProvider, parse_series
from forexintel.providers.yahoo import parse_chart


def T(s):
    return pd.Timestamp(s, tz="UTC")


def test_weekend_and_dst_aware_market_hours():
    # Friday 17:00 New York: 21:00 UTC in summer (EDT), 22:00 UTC in winter (EST)
    assert is_market_open("EURUSD", T("2026-07-10 20:55"))
    assert not is_market_open("EURUSD", T("2026-07-10 21:00"))
    assert is_market_open("EURUSD", T("2026-01-09 21:55"))
    assert not is_market_open("EURUSD", T("2026-01-09 22:00"))
    assert not is_market_open("EURUSD", T("2026-07-11 12:00"))      # Saturday
    assert not is_market_open("EURUSD", T("2026-07-12 20:55"))      # Sunday before the open
    assert is_market_open("EURUSD", T("2026-07-12 21:00"))
    assert not is_market_open("XAUUSD", T("2026-07-07 21:30"))      # gold daily break (17:00-18:00 NY)
    assert is_market_open("XAUUSD", T("2026-07-07 22:00"))


def test_gap_detection_ignores_closures_and_reports_real_gaps():
    exp = expected_index("EURUSD", T("2026-07-06 00:00"), T("2026-07-10 23:55"))
    assert exp.max() < T("2026-07-10 21:00")
    present = exp.delete(list(range(100, 112)))                       # drop 12 bars = 1 hour
    gaps = find_gaps("EURUSD", present, exp[0], exp[-1])
    assert len(gaps) == 1 and gaps[0]["bars"] == 12
    assert find_gaps("EURUSD", exp, exp[0], exp[-1]) == []


def _bi5(n=6, vol=1.0):
    recs = b"".join(struct.pack(">iiiiif", 60 * k, 110000 + k, 110002 + k, 109995 + k, 110005 + k, vol) for k in range(n))
    return lzma.decompress(lzma.compress(recs))


def test_dukascopy_decode_ordering_and_placeholder_filter():
    day = datetime(2026, 7, 6, tzinfo=timezone.utc)
    m1 = parse_bi5(_bi5(10), day, 100000.0)
    assert m1.index[0] == pd.Timestamp("2026-07-06 00:00", tz="UTC")
    assert (m1["low"] <= m1[["open", "close"]].min(axis=1)).all()
    assert (m1["high"] >= m1[["open", "close"]].max(axis=1)).all()
    assert m1["open"].iloc[0] == pytest.approx(1.10000)
    m5 = to_m5(m1)
    assert len(m5) == 2 and m5["high"].iloc[0] == pytest.approx(1.10005 + 4e-5)
    flat = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 0.0},
                        index=pd.date_range("2026-07-05", periods=10, freq="1min", tz="UTC"))
    assert to_m5(flat).empty                                           # closed-market placeholders are dropped


def test_yahoo_parser_is_mid_only_with_assumed_spread():
    payload = {"chart": {"result": [{"timestamp": [1783000000, 1783000300, 1783000600],
                                     "indicators": {"quote": [{"open": [1.10, 1.101, None], "high": [1.102, 1.103, None],
                                                               "low": [1.099, 1.100, None], "close": [1.101, 1.102, None],
                                                               "volume": [0, 0, 0]}]}}], "error": None}}
    df = parse_chart(payload, "EURUSD", now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert len(df) == 2 and not df["has_bid_ask"].any()                # null bar dropped, spread flagged assumed
    half = INSTRUMENTS["EURUSD"].assumed_spread / 2
    assert (df["ac"] - df["bc"]).iloc[0] == pytest.approx(2 * half)
    assert not get_provider("yahoo").supports("XAUUSD")               # spot gold unavailable on Yahoo


def test_oanda_parser_and_blocked_without_token():
    payload = {"candles": [{"complete": True, "volume": 12, "time": "2026-07-06T10:00:00.000000000Z",
                            "bid": {"o": "1.1000", "h": "1.1010", "l": "1.0995", "c": "1.1005"},
                            "ask": {"o": "1.1002", "h": "1.1012", "l": "1.0997", "c": "1.1007"}},
                           {"complete": False, "volume": 3, "time": "2026-07-06T10:05:00.000000000Z",
                            "bid": {"o": "1.1005", "h": "1.1008", "l": "1.1001", "c": "1.1003"},
                            "ask": {"o": "1.1007", "h": "1.1010", "l": "1.1003", "c": "1.1005"}}]}
    df = parse_candles(payload)
    assert df["has_bid_ask"].all() and df["is_complete"].tolist() == [True, False]
    assert df["ac"].iloc[0] - df["bc"].iloc[0] == pytest.approx(0.0002)
    with pytest.raises(ProviderBlocked):
        OandaProvider().fetch_candles("EURUSD", "M5", T("2026-07-01"), T("2026-07-02"))


def test_twelvedata_error_and_key_required():
    with pytest.raises(ProviderError):
        parse_series({"status": "error", "code": 400, "message": "plan does not include this symbol"}, "XAUUSD")
    with pytest.raises(ProviderBlocked):
        TwelveDataProvider().fetch_candles("EURUSD", "M5", T("2026-07-01"), T("2026-07-02"))


def test_http_retry_backoff_is_bounded(monkeypatch):
    sleeps = []

    class R:
        status_code = 503
        text = ""

    monkeypatch.setattr(pbase.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(pbase.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(ProviderError):
        pbase.http_get("http://x", tries=3)
    assert len(sleeps) == 3 and max(sleeps) <= 30


def test_every_provider_declares_capabilities_honestly():
    assert all(get_provider("dukascopy").supports(s) for s in INSTRUMENTS)
    assert get_provider("dukascopy").capabilities()["bid_ask"] is True
    assert get_provider("yahoo").capabilities()["bid_ask"] is False
    assert get_provider("twelvedata").capabilities()["bid_ask"] is False
    assert get_provider("oanda").capabilities()["verified"] is False       # never claim unverified live access
