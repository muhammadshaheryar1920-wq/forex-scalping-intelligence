"""Decision-contract tests: forecast vs trade separation, hard vetoes only, honest failure states, determinism."""
import json
import time
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from forexintel import config, db
from forexintel.data import store
from forexintel.paper import journal
from forexintel.research.dataset import prepare
from forexintel.strategy import engine
from forexintel.strategy import strategies as S
from forexintel.strategy.risk import candidate_gates, market_gates
from tests.conftest import TEST_PROVIDER, seed


def test_indicator_mismatch_is_not_a_veto():
    """Strategy triggers depend on structure/price, never on RSI/MACD agreeing."""
    m5 = seed("EURUSD", 45)
    F = prepare(m5)
    base = S.generate(F)
    G = F.copy()
    for c in ("rsi", "m15_rsi", "h1_rsi", "macd_h"):
        G[c] = 100 - G[c] if c.endswith("rsi") or c == "rsi" else -G[c]
    flipped = S.generate(G)
    for name in S.STRATEGIES:
        assert base[name][["long", "short"]].equals(flipped[name][["long", "short"]])
    assert sum(int(base[n][["long", "short"]].to_numpy().sum()) for n in S.STRATEGIES) > 20


def test_hard_gates_are_only_data_cost_news_risk():
    q_ok = {"status": "HEALTHY"}
    ok_news = {"state": "CLEAR"}
    assert market_gates(q_ok, ok_news, {"tripped": False}, ["dukascopy"])[0] == []
    assert market_gates({"status": "STALE"}, ok_news, {"tripped": False}, ["x"])[0] == ["FEED_STALE"]
    assert "NEWS_BLACKOUT" in market_gates(q_ok, {"state": "BLACKOUT"}, {"tripped": False}, ["x"])[0]
    assert "DAILY_CIRCUIT_BREAKER" in market_gates(q_ok, ok_news, {"tripped": True}, ["x"])[0]
    assert "DEMO_DATA" in market_gates(q_ok, ok_news, {"tripped": False}, ["demo"])[0]
    plan = {"ok": True, "atr": 1.0, "spread": 0.1, "reject_reason": None}
    assert candidate_gates(plan, True)[0] == []
    assert candidate_gates({**plan, "spread": 0.6}, True)[0] == ["SPREAD_TOO_WIDE"]
    assert candidate_gates(plan, False)[1] == ["SPREAD_ASSUMED_NOT_LIVE"]


def test_news_unavailable_policy_warn_vs_block(env):
    q, n = {"status": "HEALTHY"}, {"state": "UNAVAILABLE"}
    b, w = market_gates(q, n, {"tripped": False}, ["x"], replace(env, news_unavailable_policy="warn"))
    assert b == [] and w == ["NEWS_CALENDAR_UNAVAILABLE"]
    b, w = market_gates(q, n, {"tripped": False}, ["x"], replace(env, news_unavailable_policy="block"))
    assert b == ["NEWS_CALENDAR_UNAVAILABLE"]


def test_forecast_is_returned_even_when_no_trade_and_feed_stale():
    seed("EURUSD", 45)
    far = pd.Timestamp.now(tz="UTC") + timedelta(days=3)
    r = engine.analyze("EURUSD", now=far, persist=False, track_paper=False)
    assert r["trade_action"] == "NO_TRADE" and "FEED_STALE" in r["blockers"]
    f = r["directional_forecast"]
    assert f["bias"] in ("UP", "DOWN", "NEUTRAL") and f["status"] == "NOT_CALIBRATED" and f["probability"] is None
    assert r["regime"] and r["asof_utc"]


def test_cannot_assess_when_no_data():
    r = engine.analyze("GBPUSD", persist=False)
    assert r["trade_action"] == "CANNOT_ASSESS" and r["display_action"] == "CANNOT ASSESS"
    assert r["directional_forecast"]["bias"] is None and "INSUFFICIENT_DATA" in r["blockers"]


def test_demo_provider_never_authorizes_and_is_labelled(env):
    seed("EURUSD", 45, provider="demo")
    config.override(history_provider="demo")
    store._series_cache.clear()
    r = engine.analyze("EURUSD", persist=False, track_paper=False)
    assert "DEMO_DATA" in r["blockers"] and r["trade_action"] == "NO_TRADE" and r["data_mode"] == "DEMO"


def _force_trigger(monkeypatch, side=1):
    """Make every strategy silent except one that fires LONG/SHORT on the last bar."""
    def fake_generate(tail, names=None):
        out = {}
        for n in S.STRATEGIES:
            z = pd.DataFrame({"long": False, "short": False, "long_anchor": np.nan, "short_anchor": np.nan}, index=tail.index)
            if n == S.TREND_CONTINUATION:
                z.iloc[-1, z.columns.get_loc("long" if side == 1 else "short")] = True     # anchor NaN => default 1.2 ATR stop
            out[n] = z
        return out
    monkeypatch.setattr(engine.S, "generate", fake_generate)


def _edge(symbol="EURUSD", ev=0.3, n=120, positive_halves=True):
    with db.begin() as c:
        db.upsert(c, db.strategy_stats, [dict(symbol=symbol, strategy=S.TREND_CONTINUATION, computed_ts=int(time.time()), n=n, win_rate=0.45,
                                              wr_lo=.4, wr_hi=.5, ev_r=ev, ev_lo=0.05, pf=1.4, max_dd_r=5.0, run_id="t",
                                              detail={"halves_positive": positive_halves, "hold_bars_win": [2, 4, 7]})], ["symbol", "strategy"])


def test_candidate_without_validated_edge_is_wait_with_explicit_blocker(monkeypatch):
    seed("EURUSD", 45)
    _force_trigger(monkeypatch)
    r = engine.analyze("EURUSD", persist=False, track_paper=False)
    assert r["trade_action"] == "NO_TRADE" and r["display_action"] == "WAIT"
    assert "NO_VALIDATED_EDGE" in r["blockers"] and r["candidate_strategy"] == S.TREND_CONTINUATION
    assert r["sl"] is None and r["tp1"] is None            # never publish a trade plan for an unvalidated setup
    assert r["directional_forecast"]["bias"] in ("UP", "DOWN", "NEUTRAL")


@pytest.mark.parametrize("side", [1, -1])
def test_validated_edge_produces_complete_plan(monkeypatch, side):
    seed("EURUSD", 45)
    config.override(max_spread_atr=10.0)           # synthetic spreads are random; this test is about the plan, not the spread gate
    _force_trigger(monkeypatch, side)
    _edge()
    r = engine.analyze("EURUSD", persist=False, track_paper=False)
    # ignore a data-quality blocker that depends on the wall clock vs. synthetic weekend closure
    if r["blockers"] in (["MARKET_CLOSED"],):
        pytest.skip("weekend: market closed gate (correct behaviour)")
    assert r["blockers"] == [], r["blockers"]
    assert r["trade_action"] == ("LONG" if side == 1 else "SHORT")
    assert r["display_action"] in ("BUY", "STRONG BUY", "SELL", "STRONG SELL") and r["strength"] in ("MODERATE", "STRONG")
    entry = np.mean(r["entry_zone"])
    if side == 1:
        assert r["sl"] < entry < r["tp1"] and (r["tp2"] is None or r["tp1"] < r["tp2"])
    else:
        assert r["sl"] > entry > r["tp1"] and (r["tp2"] is None or r["tp1"] > r["tp2"])
    assert r["rr"]["tp1"] == pytest.approx(config.get_settings().tp1_r)
    assert (r["expected_move_pct"]["tp1"] > 0) == (side == 1)
    assert r["probability"]["status"] == "NOT_CALIBRATED" and r["probability"]["value"] is None   # no fabricated confidence
    assert pd.Timestamp(r["valid_until_utc"]) > pd.Timestamp(r["asof_utc"])
    assert r["holding_minutes"] == [10, 35]


def test_replay_is_deterministic_and_emission_is_idempotent(monkeypatch):
    seed("EURUSD", 45)
    config.override(max_spread_atr=10.0)
    _force_trigger(monkeypatch)
    _edge()
    a = engine.analyze("EURUSD")
    b = engine.analyze("EURUSD")
    strip = lambda r: {k: v for k, v in r.items() if k not in ("generated_utc",)}  # noqa: E731
    assert json.dumps(strip(a), sort_keys=True, default=str) == json.dumps(strip(b), sort_keys=True, default=str)
    with db.get_engine().connect() as c:
        assert c.execute(select(func.count()).select_from(db.signals)).scalar() == 1            # no duplicate signal row
        assert c.execute(select(func.count()).select_from(db.paper_positions)).scalar() <= 1    # no duplicate paper order


def test_circuit_breaker_and_exposure_block_new_trades(monkeypatch):
    seed("EURUSD", 45)
    config.override(max_spread_atr=10.0)
    _force_trigger(monkeypatch)
    _edge()
    now = int(time.time())
    with db.begin() as c:
        for k in range(5):
            c.execute(db.paper_positions.insert().values(id=f"cb{k}", signal_id="x", symbol="GBPUSD", side="LONG", strategy="t", authorized=True,
                                                         state="CLOSED", created_ts=now, signal_asof_ts=now, exit_ts=now, net_r=-1.0, pnl=-50.0,
                                                         risk_amount=50.0, units=1.0, detail={}))
    r = engine.analyze("EURUSD", persist=False, track_paper=True)
    if r["blockers"] == ["MARKET_CLOSED"]:
        pytest.skip("weekend")
    assert "DAILY_CIRCUIT_BREAKER" in r["blockers"] and r["trade_action"] == "NO_TRADE"


def test_news_blackout_blocks_trade(monkeypatch):
    seed("EURUSD", 45)
    config.override(max_spread_atr=10.0)
    _force_trigger(monkeypatch)
    _edge()
    now = int(time.time())
    with db.begin() as c:
        c.execute(db.economic_events.insert().values(event_id="e1", currency="EUR", title="ECB Rate Decision", scheduled_ts=now + 600,
                                                    impact="High", forecast="", previous="", actual="", published_ts=now, source="test"))
    r = engine.analyze("EURUSD", persist=False, track_paper=False)
    if r["blockers"] == ["MARKET_CLOSED"]:
        pytest.skip("weekend")
    assert "NEWS_BLACKOUT" in r["blockers"] and r["trade_action"] == "NO_TRADE"
    assert r["news"]["events"][0]["title"] == "ECB Rate Decision"
