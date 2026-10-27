"""Cost-realistic, pessimistic trade simulation on bid/ask bars (shared by backtest, labels and paper trading).

Rules (documented in docs/model-validation.md):
- Entry at the NEXT bar's open on the correct side (long at ask, short at bid) plus slippage.
- No-fill if the open is beyond the entry zone (gap > 0.25 ATR in the adverse direction).
- Long exits on BID, short exits on ASK. If SL and TP are both touched in the same bar, SL wins.
- Gap through the stop fills at the worse of stop and open. TP is a limit (no improvement credited).
- Timeout after `horizon` bars exits at the bar close (bid for long, ask for short).
- Net R = (exit - fill)/planned_risk - commission/planned_risk.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import INSTRUMENTS, Settings, get_settings


@dataclass
class Arrays:
    bo: np.ndarray
    bh: np.ndarray
    bl: np.ndarray
    bc: np.ndarray
    ao: np.ndarray
    ah: np.ndarray
    al: np.ndarray
    ac: np.ndarray

    @staticmethod
    def from_frame(F: pd.DataFrame) -> "Arrays":
        return Arrays(*(F[c].to_numpy(dtype=float) for c in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac")))


def simulate(A: Arrays, i: int, plan: dict, symbol: str, s: Settings | None = None, horizon: int | None = None) -> dict | None:
    """Simulate a plan created at the close of bar i. Returns None when there is no future bar to fill on."""
    s = s or get_settings()
    H = horizon or s.horizon_bars
    n = len(A.bo)
    j0 = i + 1
    if j0 >= n:
        return None
    side, entry_ref, sl = plan["side"], plan["entry_ref"], plan["sl"]
    tps = [plan["tp1"], plan["tp2"], plan["tp3"]]
    risk, atr = plan["risk"], plan["atr"]
    comm = INSTRUMENTS[symbol].commission_rt
    slip_f = s.slippage_spread_frac
    open_spread = A.ao[j0] - A.bo[j0]
    if side == 1:
        fill = A.ao[j0] + slip_f * open_spread
        if fill > entry_ref + 0.25 * atr:
            return {"outcome": "NOFILL"}
    else:
        fill = A.bo[j0] - slip_f * open_spread
        if fill < entry_ref - 0.25 * atr:
            return {"outcome": "NOFILL"}
    last = min(n, j0 + H) - 1
    outcome, exit_px, exit_j = "TIMEOUT", None, last
    reach2 = reach3 = False
    for j in range(j0, last + 1):
        sp = A.ac[j] - A.bc[j]
        if side == 1:
            hit_sl = A.bl[j] <= sl
            t1, t2, t3 = (A.bh[j] >= tps[0]), (tps[1] is not None and A.bh[j] >= tps[1]), (tps[2] is not None and A.bh[j] >= tps[2])
        else:
            hit_sl = A.ah[j] >= sl
            t1, t2, t3 = (A.al[j] <= tps[0]), (tps[1] is not None and A.al[j] <= tps[1]), (tps[2] is not None and A.al[j] <= tps[2])
        if hit_sl:                                   # pessimistic: stop first when ambiguous
            if outcome == "TIMEOUT":                 # position still open: stopped out
                if side == 1:
                    exit_px = min(sl, A.bo[j]) - slip_f * sp
                else:
                    exit_px = max(sl, A.ao[j]) + slip_f * sp
                outcome, exit_j = "SL", j
            break                                    # after TP1 we only keep scanning for TP2/TP3 reach until a stop
        if outcome == "TIMEOUT" and t1:
            outcome, exit_px, exit_j = "TP1", tps[0], j
        reach2 = reach2 or bool(t2)
        reach3 = reach3 or bool(t3)
    if outcome == "TIMEOUT":
        exit_px = A.bc[last] if side == 1 else A.ac[last]
    gross = (exit_px - fill) if side == 1 else (fill - exit_px)
    return {"outcome": outcome, "fill": fill, "exit": exit_px, "bars": exit_j - j0 + 1,
            "net_r": gross / risk - comm / risk, "reach_tp2": reach2, "reach_tp3": reach3,
            "y": 1 if outcome == "TP1" else 0}
