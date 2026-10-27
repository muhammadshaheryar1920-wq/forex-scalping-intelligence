"""Paper trading journal: idempotent positions, same fill/exit rules as the backtest, daily circuit breaker.

PAPER ONLY. Nothing here places real orders.
"""
from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd
from sqlalchemy import select, update

from .. import db
from ..config import INSTRUMENTS, get_settings
from ..research.metrics import summarize
from ..research.simulate import Arrays, simulate
from ..strategy.plan import position_size


def position_id(signal_id: str, strategy: str, side: int) -> str:
    return f"{signal_id}:{strategy}:{'L' if side == 1 else 'S'}"


def open_position(signal_id: str, symbol: str, asof_ts: int, plan: dict, strategy: str, authorized: bool) -> str | None:
    """Create a PENDING paper position (fills on the next bar's open). Returns None if it already exists."""
    s = get_settings()
    pid = position_id(signal_id, strategy, plan["side"])
    size = position_size(symbol, s.paper_equity, s.risk_per_trade_pct, plan["risk"])
    row = dict(id=pid, signal_id=signal_id, symbol=symbol, side="LONG" if plan["side"] == 1 else "SHORT", strategy=strategy,
               authorized=bool(authorized), state="PENDING", created_ts=int(time.time()), signal_asof_ts=int(asof_ts),
               entry_price=None, sl=plan["sl"], tp1=plan["tp1"], tp2=plan["tp2"], tp3=plan["tp3"], units=size["units"],
               risk_amount=size["risk_amount"], detail={"plan": {k: (None if v is None else float(v) if isinstance(v, (int, float, np.floating)) else v)
                                                                 for k, v in plan.items()}})
    with db.begin() as c:
        exists = c.execute(select(db.paper_positions.c.id).where(db.paper_positions.c.id == pid)).first()
        if exists:
            return None
        c.execute(db.paper_positions.insert().values(**row))
    return pid


def _positions(states=None, symbol=None, authorized=None) -> list[dict]:
    p = db.paper_positions
    q = select(p).order_by(p.c.created_ts.desc())
    if states:
        q = q.where(p.c.state.in_(states))
    if symbol:
        q = q.where(p.c.symbol == symbol)
    if authorized is not None:
        q = q.where(p.c.authorized.is_(authorized))
    with db.get_engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def list_positions(limit: int = 200, symbol: str | None = None) -> list[dict]:
    return _positions(symbol=symbol)[:limit]


def advance(symbol: str, m5: pd.DataFrame) -> dict:
    """Resolve PENDING/OPEN positions of `symbol` against closed bars using the shared simulator."""
    s = get_settings()
    stats = {"filled": 0, "closed": 0, "nofill": 0}
    pend = _positions(("PENDING", "OPEN"), symbol)
    if not pend or m5.empty:
        return stats
    A = Arrays.from_frame(m5)
    ts_epoch = (m5.index.astype("int64") // 10**9).to_numpy()
    n = len(m5)
    for pos in pend:
        i = int(np.searchsorted(ts_epoch, pos["signal_asof_ts"]))
        if i >= n or ts_epoch[i] != pos["signal_asof_ts"]:
            continue
        plan = pos["detail"]["plan"]
        res = simulate(A, i, plan, symbol, s)
        if res is None:
            continue                                        # fill bar not closed yet
        upd: dict = {}
        if res["outcome"] == "NOFILL":
            upd.update(state="NOFILL", exit_reason="NOFILL", net_r=0.0, pnl=0.0)
            stats["nofill"] += 1
        else:
            exit_j = i + res["bars"]
            finished = res["outcome"] in ("TP1", "SL") or (i + s.horizon_bars) <= n - 1
            if not finished:
                if pos["state"] != "OPEN":
                    upd.update(state="OPEN", entry_ts=int(ts_epoch[i + 1]), entry_price=res["fill"])
                    stats["filled"] += 1
            else:
                net_r = float(res["net_r"])
                upd.update(state="CLOSED", entry_ts=int(ts_epoch[i + 1]), entry_price=res["fill"], exit_ts=int(ts_epoch[min(exit_j, n - 1)]) + 300,
                           exit_price=res["exit"], exit_reason=res["outcome"], net_r=net_r, pnl=net_r * pos["risk_amount"],
                           fees=INSTRUMENTS[symbol].commission_rt * pos["units"])
                d = dict(pos["detail"])
                d.update(bars=res["bars"], reach_tp2=bool(res["reach_tp2"]), reach_tp3=bool(res["reach_tp3"]))
                upd["detail"] = d
                stats["closed"] += 1
        if upd:
            with db.begin() as c:
                c.execute(update(db.paper_positions).where(db.paper_positions.c.id == pos["id"]).values(**upd))
    return stats


def todays_realized_r(now=None) -> float:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    day0 = int(now.normalize().timestamp())
    rows = [p for p in _positions(("CLOSED",), authorized=True) if (p["exit_ts"] or 0) >= day0]
    return float(sum(p["net_r"] or 0.0 for p in rows))


def circuit_breaker(now=None) -> dict:
    s = get_settings()
    r = todays_realized_r(now)
    loss_pct = -r * s.risk_per_trade_pct
    return {"tripped": loss_pct >= s.daily_loss_limit_pct, "today_r": r, "today_loss_pct": max(0.0, loss_pct),
            "limit_pct": s.daily_loss_limit_pct}


def exposure_blockers(symbol: str, side: int, exclude_signal_id: str | None = None) -> list[str]:
    """One active authorized position per symbol; at most two same-direction USD exposures (all 3 pairs quote USD).
    Positions created by `exclude_signal_id` itself are ignored so re-analysing a bar is repeatable."""
    out = []
    active = [p for p in _positions(("PENDING", "OPEN"), authorized=True) if p["signal_id"] != exclude_signal_id]
    if any(p["symbol"] == symbol for p in active):
        out.append("POSITION_ALREADY_OPEN")
    same_usd = [p for p in active if (1 if p["side"] == "LONG" else -1) == side and p["symbol"] != symbol]
    if len(same_usd) >= 2:
        out.append("CORRELATED_USD_EXPOSURE")
    return out


def performance(authorized: bool | None = True, symbol: str | None = None) -> dict:
    rows = [p for p in _positions(("CLOSED",), symbol, authorized)]
    r = np.array([p["net_r"] for p in rows if p["net_r"] is not None], dtype=float)
    wins = np.array([1 if p["exit_reason"] == "TP1" else 0 for p in rows if p["net_r"] is not None])
    out = summarize(r, wins)
    out["open"] = len(_positions(("PENDING", "OPEN"), symbol, authorized))
    out["pnl_usd"] = float(sum(p["pnl"] or 0 for p in rows))
    return out
