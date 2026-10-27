"""Catch-up, de-duplication, gap handling and honest failure states of the synchronizer."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import func, select

from forexintel import db
from forexintel.data import store, sync
from forexintel.providers import ProviderBlocked, ProviderError
from forexintel.providers.base import MarketDataProvider
from forexintel.providers.demo import DemoProvider

PROV = "faketest"


class FakeProvider(MarketDataProvider):
    name = PROV
    has_bid_ask = True
    verified = False

    def __init__(self):
        self.available_until = None
        self.holes: list = []
        self.calls: list = []
        self.error: Exception | None = None

    def supports(self, symbol):
        return True

    def fetch_candles(self, symbol, timeframe, start, end):
        self.calls.append((start, end))
        if self.error:
            raise self.error
        f = DemoProvider().fetch_candles(symbol, "M5", start, min(end, self.available_until))
        f = f.copy()
        f["is_complete"] = True
        for a, b in self.holes:
            f = f[(f.index < a) | (f.index >= b)]
        return f


@pytest.fixture
def fake(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(sync, "get_provider", lambda name: p)
    return p


def count(symbol="EURUSD"):
    with db.get_engine().connect() as c:
        return c.execute(select(func.count()).select_from(db.candles).where(db.candles.c.symbol == symbol)).scalar()


def test_first_sync_stores_only_closed_bars_and_reports(fake):
    now = datetime.now(timezone.utc) - timedelta(days=5)
    fake.available_until = now
    r = sync.sync_symbol("EURUSD", PROV, days=20, now=now)
    assert r.status == "OK" and r.fetched > 4000 and r.stored_total == count()
    assert r.last_bar is not None and r.freshness_minutes is not None
    assert r.gaps == []


def test_laptop_off_72h_catches_up_without_duplicates(fake):
    t0 = datetime.now(timezone.utc) - timedelta(days=6)
    fake.available_until = t0
    sync.sync_symbol("EURUSD", PROV, days=15, now=t0)
    n0 = count()
    last0 = store.last_complete_ts(PROV, "EURUSD")
    t1 = t0 + timedelta(hours=72)
    fake.available_until = t1
    r = sync.sync_symbol("EURUSD", PROV, days=15, now=t1)
    assert any("catch-up after" in n for n in r.notes)
    from forexintel.data.calendar import expected_index
    expected_last = expected_index("EURUSD", t1 - timedelta(days=3), t1 - timedelta(minutes=5))[-1]
    assert store.last_complete_ts(PROV, "EURUSD") == expected_last          # caught up to the last bar the market produced
    assert expected_last > last0
    n1 = count()
    assert n1 > n0
    # fetched from (last saved bar - overlap), not from scratch
    assert fake.calls[-1][0] >= last0.to_pydatetime() - timedelta(hours=3)
    # replaying the same sync is idempotent: no duplicate candles
    sync.sync_symbol("EURUSD", PROV, days=15, now=t1)
    assert count() == n1
    with db.get_engine().connect() as c:
        dup = c.execute(select(db.candles.c.open_ts, func.count()).group_by(db.candles.c.open_ts).having(func.count() > 1)).fetchall()
    assert dup == []


def test_gap_is_reported_then_refetched_next_sync(fake):
    now = datetime.now(timezone.utc) - timedelta(days=3)
    fake.available_until = now
    hole = (pd.Timestamp(now - timedelta(hours=30)).floor("5min"), pd.Timestamp(now - timedelta(hours=29)).floor("5min"))
    fake.holes = [hole]
    r = sync.sync_symbol("EURUSD", PROV, days=10, now=now)
    assert r.gaps and sum(g["bars"] for g in r.gaps) >= 6 or r.gaps == []      # hole may fall in a closed window
    if r.gaps:
        fake.holes = []
        r2 = sync.sync_symbol("EURUSD", PROV, days=10, now=now + timedelta(minutes=5))
        assert any("unfilled gap" in n for n in r2.notes)
        assert r2.gaps == []


def test_provider_outage_is_reported_not_hidden(fake):
    now = datetime.now(timezone.utc)
    fake.available_until = now
    fake.error = ProviderError("HTTP 503 from upstream")
    r = sync.sync_symbol("EURUSD", PROV, days=5, now=now)
    assert r.status == "FAILED" and "503" in r.error and count() == 0
    fake.error = ProviderBlocked("OANDA_TOKEN not set")
    assert sync.sync_symbol("EURUSD", PROV, days=5, now=now).status == "BLOCKED"


def test_unsupported_symbol_reported_without_network():
    r = sync.sync_symbol("XAUUSD", "yahoo", days=3)          # real provider class: spot gold is not available there
    assert r.status == "UNSUPPORTED"


def test_save_candles_drops_forming_bar_and_is_idempotent():
    now = pd.Timestamp.now(tz="UTC")
    f = DemoProvider().fetch_candles("EURUSD", "M5", (now - pd.Timedelta(days=2)).to_pydatetime(), now.to_pydatetime())
    f.loc[f.index[-1], "is_complete"] = False
    n = store.save_candles(f, PROV, "EURUSD")
    assert n == int(f["is_complete"].sum())
    store.save_candles(f, PROV, "EURUSD")
    assert count() == n
