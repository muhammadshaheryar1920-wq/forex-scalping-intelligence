"""Leakage / point-in-time tests: a decision at bar t may only use information available at t's close."""
import pandas as pd

from tests.conftest import seed
from forexintel.data.store import resample
from forexintel.features.builder import FEATURE_COLS, _base, build_features


def test_features_do_not_change_when_future_bars_are_removed():
    m5 = seed("EURUSD", 45)
    full = build_features(m5)
    for back in (900, 337, 41):
        T = len(m5) - back
        part = build_features(m5.iloc[:T + 1])
        a, b = full.iloc[T][FEATURE_COLS].astype(float), part.iloc[-1][FEATURE_COLS].astype(float)
        assert ((a - b).abs().fillna(0) < 1e-9).all()
        assert (a.isna() == b.isna()).all()


def test_higher_timeframe_bar_appears_only_after_it_closes():
    m5 = seed("EURUSD", 20)
    last_close = m5.index[-1] + pd.Timedelta(minutes=5)
    h1 = resample(m5, "H1")
    assert (h1.index + pd.Timedelta(hours=1) <= last_close).all()          # no forming H1 bar
    m15 = resample(m5, "M15")
    assert (m15.index + pd.Timedelta(minutes=15) <= last_close).all()


def test_h1_context_used_at_m5_decision_time_is_closed():
    m5 = seed("EURUSD", 30)
    f = build_features(m5)
    h1 = resample(m5, "H1")
    ts = f.index[len(f) // 2].floor("1h") + pd.Timedelta(minutes=25)
    ts = f.index[f.index.get_indexer([ts], method="nearest")[0]]
    decision = ts + pd.Timedelta(minutes=5)
    closed = h1[h1.index + pd.Timedelta(hours=1) <= decision]
    b, *_ = _base(closed, 2)
    assert f.loc[ts, "h1_trend"] == b["trend"].iloc[-1]
