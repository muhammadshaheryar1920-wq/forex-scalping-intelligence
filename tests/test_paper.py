import time

import numpy as np
import pandas as pd
import pytest

from forexintel import db
from forexintel.paper import journal

T0 = pd.Timestamp("2026-07-06 10:00", tz="UTC")


def frame(rows):
    """rows: (bid_o, bid_h, bid_l, bid_c, spread) per 5-minute bar starting at T0."""
    a = np.array(rows, dtype=float)
    bo, bh, bl, bc, sp = a.T
    idx = pd.date_range(T0, periods=len(rows), freq="5min")
    return pd.DataFrame({"bo": bo, "bh": bh, "bl": bl, "bc": bc, "ao": bo + sp, "ah": bh + sp, "al": bl + sp, "ac": bc + sp}, index=idx)


def plan_long():
    return {"side": 1, "entry_ref": 1.1000, "sl": 1.0990, "tp1": 1.1010, "tp2": 1.1018, "tp3": 1.1028, "risk": 0.0010, "atr": 0.0007,
            "entry_lo": 1.1, "entry_hi": 1.1004, "ok": True, "reject_reason": None}


def open_pos(authorized=True, sid="s1", asof=None):
    return journal.open_position(sid, "EURUSD", int((asof or T0).timestamp()), plan_long(), "TREND_CONTINUATION", authorized)


def test_position_creation_is_idempotent_and_sized_by_risk():
    pid = open_pos()
    assert pid and open_pos() is None                                  # restart/reconnect cannot duplicate an order
    pos = journal.list_positions()[0]
    assert pos["risk_amount"] == pytest.approx(10000 * 0.5 / 100)
    assert pos["units"] == pytest.approx(50 / 0.0010)


def test_advance_fills_next_bar_open_and_closes_at_tp1():
    open_pos()
    m = frame([(1.0995, 1.1001, 1.0994, 1.0999, 0.00002), (1.1000, 1.1006, 1.0998, 1.1004, 0.00002), (1.1004, 1.1013, 1.1003, 1.1011, 0.00002)])
    st = journal.advance("EURUSD", m)
    pos = journal.list_positions()[0]
    assert pos["state"] == "CLOSED" and pos["exit_reason"] == "TP1" and pos["net_r"] > 0.8
    assert pos["entry_price"] == pytest.approx(1.1000 + 0.00002 + 0.25 * 0.00002, abs=1e-6)   # ask open + slippage
    assert st["closed"] == 1


def test_position_stays_open_until_resolved_then_stops_out():
    open_pos()
    m = frame([(1.0995, 1.1001, 1.0994, 1.0999, 0.00002), (1.1000, 1.1005, 1.0996, 1.1002, 0.00002)])
    journal.advance("EURUSD", m)
    assert journal.list_positions()[0]["state"] == "OPEN"
    m2 = frame([(1.0995, 1.1001, 1.0994, 1.0999, 0.00002), (1.1000, 1.1005, 1.0996, 1.1002, 0.00002), (1.1002, 1.1003, 1.0985, 1.0987, 0.00002)])
    journal.advance("EURUSD", m2)
    pos = journal.list_positions()[0]
    assert pos["state"] == "CLOSED" and pos["exit_reason"] == "SL" and pos["net_r"] < -0.9


def test_unfillable_gap_is_recorded_as_nofill():
    open_pos()
    m = frame([(1.0995, 1.1001, 1.0994, 1.0999, 0.00002), (1.1020, 1.1030, 1.1015, 1.1025, 0.00002)])
    journal.advance("EURUSD", m)
    assert journal.list_positions()[0]["state"] == "NOFILL"


def test_circuit_breaker_trips_at_daily_loss_limit_and_ignores_shadow():
    now = int(time.time())

    def closed(i, r, auth):
        with db.begin() as c:
            c.execute(db.paper_positions.insert().values(id=f"c{i}", signal_id="x", symbol="EURUSD", side="LONG", strategy="t", authorized=auth,
                                                         state="CLOSED", created_ts=now, signal_asof_ts=now, exit_ts=now, net_r=r, pnl=r * 50,
                                                         risk_amount=50.0, units=1.0, detail={}))
    for k in range(3):
        closed(k, -1.0, True)
    assert not journal.circuit_breaker()["tripped"]                    # 3R * 0.5% = 1.5% < 2%
    closed(10, -5.0, False)                                            # shadow losses never count
    assert not journal.circuit_breaker()["tripped"]
    closed(11, -1.0, True)
    assert journal.circuit_breaker()["tripped"]                        # 4R * 0.5% = 2.0%


def test_exposure_rules():
    open_pos(sid="a")
    assert "POSITION_ALREADY_OPEN" in journal.exposure_blockers("EURUSD", 1)
    assert journal.exposure_blockers("EURUSD", 1, exclude_signal_id="a") == []
    plan = plan_long()
    journal.open_position("b", "GBPUSD", int(T0.timestamp()), plan, "TREND_CONTINUATION", True)
    journal.open_position("c", "XAUUSD", int(T0.timestamp()), plan, "TREND_CONTINUATION", True)
    assert "CORRELATED_USD_EXPOSURE" in journal.exposure_blockers("EURUSD", 1, exclude_signal_id="a")
    assert "CORRELATED_USD_EXPOSURE" not in journal.exposure_blockers("EURUSD", -1, exclude_signal_id="a")


def test_performance_summary_separates_authorized_and_shadow():
    open_pos(authorized=False, sid="shadow")
    m = frame([(1.0995, 1.1001, 1.0994, 1.0999, 0.00002), (1.1000, 1.1006, 1.0998, 1.1004, 0.00002), (1.1004, 1.1013, 1.1003, 1.1011, 0.00002)])
    journal.advance("EURUSD", m)
    assert journal.performance(False)["n"] == 1 and journal.performance(True)["n"] == 0
