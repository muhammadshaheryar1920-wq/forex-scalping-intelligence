"""Performance statistics with uncertainty (win rate Wilson CI, expectancy CI, profit factor, drawdown)."""
from __future__ import annotations

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def max_drawdown(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float((peak - eq).max())


def summarize(net_r, wins=None) -> dict:
    r = np.asarray(net_r, dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0, "win_rate": None, "wr_lo": None, "wr_hi": None, "ev_r": None, "ev_lo": None, "ev_hi": None,
                "pf": None, "max_dd_r": 0.0, "total_r": 0.0}
    w = (np.asarray(wins) == 1) if wins is not None else (r > 0)
    k = int(w.sum())
    lo, hi = wilson(k, n)
    mean = float(r.mean())
    se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": int(n), "win_rate": k / n, "wr_lo": lo, "wr_hi": hi, "ev_r": mean,
            "ev_lo": mean - 1.96 * se if n > 1 else None, "ev_hi": mean + 1.96 * se if n > 1 else None,
            "pf": float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
            "max_dd_r": max_drawdown(r), "total_r": float(r.sum())}


def clean(obj):
    """JSON-safe conversion (NaN/inf -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    return obj
