import numpy as np
import pandas as pd
import pytest

from forexintel.config import INSTRUMENTS, Settings, get_settings
from forexintel.research.simulate import Arrays, simulate
from forexintel.strategy.plan import expected_move_pct, make_plan, position_size


def bars(rows):
    """rows: (bid_o, bid_h, bid_l, bid_c, spread). Ask = bid + spread."""
    a = np.array(rows, dtype=float)
    bo, bh, bl, bc, sp = a.T
    return Arrays(bo, bh, bl, bc, bo + sp, bh + sp, bl + sp, bc + sp)


def row(entry_ask=100.0, bid=99.9, atr=1.0, **kw):
    d = {"atr": atr, "spread": entry_ask - bid, "ac": entry_ask, "bc": bid}
    d.update(kw)
    return pd.Series(d)


def plan_long(sl=98.0, tp1=102.0, tp2=103.8, tp3=105.6, entry=100.0, atr=1.0):
    return {"side": 1, "entry_ref": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk": entry - sl, "atr": atr}


def test_long_hits_tp1_and_costs_reduce_r():
    A = bars([(99, 99.5, 98.5, 99.0, .1), (99.9, 100.5, 99.5, 100.2, .1), (100.2, 102.6, 100, 102.4, .1)])
    r = simulate(A, 0, plan_long(), "EURUSD")
    assert r["outcome"] == "TP1" and r["y"] == 1
    assert 0.9 < r["net_r"] < 1.1                      # ~ +1R minus tiny commission/spread effects


def test_same_bar_stop_and_target_resolves_against_trader():
    A = bars([(99, 99.5, 98.5, 99.0, .1), (99.9, 103.0, 97.5, 100.0, .1)])
    r = simulate(A, 0, plan_long(), "EURUSD")
    assert r["outcome"] == "SL" and r["y"] == 0 and r["net_r"] < -0.9


def test_gap_through_stop_fills_worse_than_stop():
    A = bars([(99, 99.5, 98.5, 99.0, .1), (99.9, 100, 99.8, 99.9, .1), (97.0, 97.5, 96.5, 97.0, .1)])
    r = simulate(A, 0, plan_long(), "EURUSD")
    assert r["outcome"] == "SL" and r["exit"] < 98.0 and r["net_r"] < -1.2


def test_short_side_uses_ask_for_stop_and_bid_for_entry():
    p = {"side": -1, "entry_ref": 100.0, "sl": 102.0, "tp1": 98.0, "tp2": 96.2, "tp3": 94.4, "risk": 2.0, "atr": 1.0}
    A = bars([(100.1, 100.2, 99.9, 100.0, .1), (100.0, 100.1, 97.8, 98.0, .1)])
    r = simulate(A, 0, p, "EURUSD")
    assert r["outcome"] == "TP1"
    A2 = bars([(100.1, 100.2, 99.9, 100.0, .1), (100.0, 101.95, 99.5, 100.0, 0.2)])   # ask high = 102.15 >= SL
    assert simulate(A2, 0, p, "EURUSD")["outcome"] == "SL"


def test_timeout_exits_at_close_and_nofill_on_adverse_gap():
    flat = [(99.9, 100.1, 99.8, 100.0, .1)] * 14
    r = simulate(bars([(99, 99.5, 98.5, 99.0, .1)] + flat), 0, plan_long(), "EURUSD", horizon=12)
    assert r["outcome"] == "TIMEOUT" and r["bars"] == 12
    gap = simulate(bars([(99, 99.5, 98.5, 99.0, .1), (101.0, 101.5, 100.5, 101.0, .1)]), 0, plan_long(), "EURUSD")
    assert gap["outcome"] == "NOFILL"
    assert simulate(bars([(99, 99.5, 98.5, 99.0, .1)]), 0, plan_long(), "EURUSD") is None   # no future bar yet


def test_tp2_tp3_reach_flags_ignore_bars_after_a_stop():
    A = bars([(99, 99.5, 98.5, 99.0, .1), (99.9, 102.1, 99.5, 102.0, .1), (102, 104, 101.9, 103.9, .1), (103.9, 106, 103, 105.9, .1)])
    r = simulate(A, 0, plan_long(), "EURUSD")
    assert r["outcome"] == "TP1" and r["reach_tp2"] and r["reach_tp3"]
    B = bars([(99, 99.5, 98.5, 99.0, .1), (99.9, 102.1, 99.5, 102.0, .1), (102, 102.2, 97.0, 98, .1), (98, 106, 97, 105, .1)])
    r2 = simulate(B, 0, plan_long(), "EURUSD")
    assert r2["outcome"] == "TP1" and not r2["reach_tp3"]                   # stop hit first, later spike not credited


def test_plan_geometry_long_and_short():
    s = get_settings()
    r = make_plan(row(), 1, 99.0, s, "EURUSD")
    assert r["sl"] < r["entry_ref"] < r["tp1"]
    assert r["tp1"] - r["entry_ref"] == pytest.approx(s.tp1_r * r["risk"])
    r2 = make_plan(row(entry_ask=100.1, bid=100.0), -1, 101.0, s, "EURUSD")
    assert r2["tp1"] < r2["entry_ref"] < r2["sl"]
    assert r["cost_r"] > 0


def test_plan_rejects_too_wide_stop_and_widens_too_tight():
    s = get_settings()
    wide = make_plan(row(), 1, 90.0, s, "EURUSD")
    assert not wide["ok"] and wide["reject_reason"] == "SL_TOO_WIDE"
    tight = make_plan(row(), 1, 99.95, s, "EURUSD")
    assert tight["risk"] >= s.sl_atr_min * 1.0 - 1e-9


def test_structure_blocks_tp2_when_path_is_obstructed():
    s = get_settings()
    free = make_plan(row(), 1, 99.0, s, "EURUSD")
    blocked = make_plan(row(sh_level=100.6), 1, 99.0, s, "EURUSD")
    assert free["tp2"] is not None
    assert blocked["tp2"] is None and blocked["tp3"] is None             # nearest resistance is closer than 1.3R


def test_expected_move_pct_formula():
    assert expected_move_pct(100.0, 101.0) == pytest.approx(1.0)
    assert expected_move_pct(2000.0, 1990.0) == pytest.approx(-0.5)       # short target is a negative move


def test_position_sizing_matches_contract_specs():
    fx = position_size("EURUSD", 10000, 0.5, 0.0010)       # risk $50 over 10 pips
    assert fx["units"] == pytest.approx(50 / 0.0010) and fx["lots"] == pytest.approx(0.5)
    gold = position_size("XAUUSD", 10000, 0.5, 5.0)        # risk $50 over $5 stop => 10 oz = 0.1 lot
    assert gold["units"] == pytest.approx(10.0) and gold["lots"] == pytest.approx(0.1)
