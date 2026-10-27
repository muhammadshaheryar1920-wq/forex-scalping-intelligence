"""Trade-plan construction: entry zone, structure-aware SL, R-multiple targets, cost model.

R is always measured against the PLANNED risk (entry_ref - SL). Long trades enter at ASK and exit at BID
(short: enter BID, exit ASK), so spread is paid inside the fills; commission and slippage are added in cost_r.
"""
from __future__ import annotations

import math

import numpy as np

from ..config import INSTRUMENTS, Settings, get_settings


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def make_plan(row, side: int, anchor, settings: Settings | None = None, symbol: str | None = None) -> dict:
    s = settings or get_settings()
    ins = INSTRUMENTS[symbol] if symbol else None
    atr = float(row["atr"])
    spread = float(row["spread"])
    entry = float(row["ac"] if side == 1 else row["bc"])
    min_stop = max(ins.min_stop if ins else 0.0, 3.0 * spread, s.sl_atr_min * atr)
    if _ok(anchor) and ((side == 1 and anchor < entry) or (side == -1 and anchor > entry)):
        dist = abs(entry - float(anchor)) + 0.1 * atr
    else:
        dist = s.sl_atr_mult * atr
    reason = None
    if dist > s.sl_atr_max * atr:
        reason = "SL_TOO_WIDE"
    dist = max(dist, min_stop)
    sl = entry - side * dist
    tp = [entry + side * m * dist for m in (s.tp1_r, s.tp2_r, s.tp3_r)]
    levels = _levels(row, side, entry)
    tp2, tp3 = _structure_targets(levels, side, entry, dist, tp, s)
    slip = s.slippage_spread_frac * spread
    comm = ins.commission_rt if ins else 0.0
    cost_r = (comm + 2.0 * slip) / dist
    return {
        "side": side, "entry_ref": entry, "entry_lo": entry if side == 1 else entry - 0.25 * atr,
        "entry_hi": entry + 0.25 * atr if side == 1 else entry, "sl": sl, "tp1": tp[0], "tp2": tp2, "tp3": tp3,
        "risk": dist, "risk_atr": dist / atr, "cost_r": cost_r, "spread": spread, "atr": atr,
        "rr1": s.tp1_r, "rr2": None if tp2 is None else abs(tp2 - entry) / dist,
        "rr3": None if tp3 is None else abs(tp3 - entry) / dist,
        "ok": reason is None, "reject_reason": reason,
    }


def _levels(row, side: int, entry: float) -> list[float]:
    keys = ("sh_level", "m15_sh", "h1_sh", "asia_hi") if side == 1 else ("sl_level", "m15_sl", "h1_sl", "asia_lo")
    out = []
    for k in keys:
        v = row.get(k) if hasattr(row, "get") else row[k]
        if _ok(v) and not (isinstance(v, float) and np.isnan(v)):
            v = float(v)
            if (v - entry) * side > 0:
                out.append(v)
    return sorted(out, key=lambda x: abs(x - entry))


def _structure_targets(levels, side, entry, dist, tp, s):
    """TP2/TP3 are shown only when the path is not blocked by structure closer than the R target."""
    def free_or_capped(target, floor_r):
        blockers = [lv for lv in levels if 0 < (lv - entry) * side < (target - entry) * side]
        if not blockers:
            return target
        cap = blockers[0]
        return cap if abs(cap - entry) / dist >= floor_r else None
    tp2 = free_or_capped(tp[1], 1.3)
    tp3 = free_or_capped(tp[2], 2.0) if tp2 is not None else None
    if tp2 is not None and tp3 is not None and abs(tp3 - tp2) < 0.4 * dist:
        tp3 = None
    return tp2, tp3


def expected_move_pct(entry: float, target: float) -> float:
    """100 * (target - reference) / reference, signed (negative for a short's target)."""
    return 100.0 * (target - entry) / entry


def position_size(symbol: str, equity: float, risk_pct: float, risk_distance: float) -> dict:
    """Paper sizing for USD-quoted instruments: P&L(USD) = units * price change, so units = risk$/stop distance."""
    ins = INSTRUMENTS[symbol]
    risk_amount = equity * risk_pct / 100.0
    units = risk_amount / risk_distance if risk_distance > 0 else 0.0
    return {"risk_amount": risk_amount, "units": units, "lots": units / ins.contract_size}
