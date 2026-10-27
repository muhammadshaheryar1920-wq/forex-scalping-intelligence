"""Backtest + walk-forward analysis on bid/ask bars with spread, slippage and commission (see simulate.py)."""
from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd

from .. import db
from ..config import Settings, get_settings, normalize_symbol
from ..data import quality, store
from . import registry  # noqa: F401  (kept for symmetry with train)
from .dataset import build_events, dataset_hash, model_matrix, pick_non_overlapping, prepare
from .metrics import clean, summarize
from .models import ALGOS, fit_select

SESSION_COLS = [("in_london", "LONDON"), ("in_ny", "NEW_YORK"), ("in_asia", "ASIA")]
EMBARGO = 6


def _stats(ev: pd.DataFrame) -> dict:
    return summarize(ev["net_r"].to_numpy(), ev["y"].to_numpy()) if len(ev) else summarize([])


def _breakdowns(po: pd.DataFrame, F: pd.DataFrame) -> dict:
    if po.empty:
        return {}
    d = po.copy()
    sess = np.select([F["overlap_ln"].to_numpy()[d["i"]] == 1, F["in_london"].to_numpy()[d["i"]] == 1,
                      F["in_ny"].to_numpy()[d["i"]] == 1, F["in_asia"].to_numpy()[d["i"]] == 1],
                     ["LONDON_NY_OVERLAP", "LONDON", "NEW_YORK", "ASIA"], "OFF_HOURS")
    d["session"] = sess
    d["month"] = F.index[d["i"]].strftime("%Y-%m")
    d["side_s"] = np.where(d["side"] == 1, "LONG", "SHORT")
    out = {}
    for key in ("session", "base_regime", "month", "side_s", "strategy"):
        out[key] = {str(k): _stats(g) for k, g in d.groupby(key)}
    return out


def _equity(po: pd.DataFrame, F: pd.DataFrame, max_points: int = 400) -> list:
    if po.empty:
        return []
    po = po.sort_values("i")
    eq = po["net_r"].cumsum().to_numpy()
    ts = F.index[po["i"].to_numpy()]
    step = max(1, len(eq) // max_points)
    return [[ts[k].isoformat(), float(eq[k])] for k in range(0, len(eq), step)] + [[ts[-1].isoformat(), float(eq[-1])]]


def walk_forward(ev: pd.DataFrame, X: pd.DataFrame, H: int, algos=("lightgbm", "logistic"), folds: int = 5,
                 init_frac: float = 0.5, seed: int = 7) -> dict:
    n = len(ev)
    i = ev["i"].to_numpy()
    y, r = ev["y"].to_numpy(), ev["net_r"].to_numpy()
    cols = list(X.columns)
    edges = [int(n * (init_frac + (1 - init_frac) * k / folds)) for k in range(folds + 1)]
    gap = H + 2 + EMBARGO
    out = {a: {"folds": [], "selected": []} for a in algos}
    base_sel = []
    for k in range(folds):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        t0 = i[a]
        te = (np.arange(n) >= a) & (np.arange(n) < b)
        train_idx = np.flatnonzero(i < t0 - gap)
        if len(train_idx) < 200:
            continue
        m = len(train_idx)
        cut1, cut2 = int(m * 0.70), int(m * 0.85)
        tr = train_idx[: np.searchsorted(i[train_idx], i[train_idx[cut1]] - gap)]
        ca = train_idx[(i[train_idx] >= i[train_idx[cut1]]) & (i[train_idx] < i[train_idx[cut2]] - gap)]
        th = train_idx[i[train_idx] >= i[train_idx[cut2]]]
        base_sel.append(ev[te])
        for algo in algos:
            bundle = fit_select(X.iloc[tr], y[tr], r[tr], X.iloc[ca], y[ca], X.iloc[th], r[th], y[th], algo, cols, seed)
            fold = {"fold": k + 1, "test_from": int(t0), "events": int(te.sum()), "threshold": bundle.threshold,
                    "baseline": _stats(pick_non_overlapping(ev[te]))}
            if bundle.threshold is None:
                fold["selected"] = summarize([])
            else:
                p = bundle.proba(X[te])
                sel = ev[te][p >= bundle.threshold]
                out[algo]["selected"].append(sel)
                fold["selected"] = _stats(pick_non_overlapping(sel))
            out[algo]["folds"].append(fold)
    res = {"baseline_oos": _stats(pick_non_overlapping(pd.concat(base_sel))) if base_sel else summarize([]), "models": {}}
    for algo, d in out.items():
        sel = pd.concat(d["selected"]) if d["selected"] else pd.DataFrame(columns=ev.columns)
        res["models"][algo] = {"oos_selected": _stats(pick_non_overlapping(sel)) if len(sel) else summarize([]),
                               "folds": d["folds"], "folds_positive": int(sum(1 for f in d["folds"]
                                                                              if (f["selected"]["ev_r"] or 0) > 0))}
    return res


def run_backtest(symbol: str, walk_forward_on: bool = True, s: Settings | None = None, algos=("lightgbm", "logistic")) -> dict:
    s = s or get_settings()
    symbol = normalize_symbol(symbol)
    m5, meta = store.load_series(symbol)
    rep: dict = {"symbol": symbol, "source": meta, "bars": int(len(m5)), "status": "NOT_RUN"}
    if len(m5) < quality.MIN_BARS:
        rep["reason"] = f"only {len(m5)} bars (< {quality.MIN_BARS}); run sync first"
        return rep
    F = prepare(m5)
    ev, stats = build_events(F, symbol, s)
    rep.update(event_stats=stats, events=int(len(ev)), data_from=m5.index[0].isoformat(), data_to=m5.index[-1].isoformat(),
               dataset_hash=dataset_hash(m5), assumed_spread_bars=meta.get("assumed_spread_bars"),
               costs={"spread": "real bid/ask" if meta.get("assumed_spread_bars", 0) == 0 else "partly ASSUMED (mid-only source)",
                      "slippage_spread_frac": s.slippage_spread_frac, "same_bar_tie": "stop first", "news_filter": "NOT applied in backtests (no point-in-time history)"})
    if len(ev) == 0:
        rep["reason"] = "no candidate events"
        return rep
    pooled = pick_non_overlapping(ev)
    rep["pooled_rules_only"] = _stats(pooled)
    rep["breakdowns"] = _breakdowns(pooled, F)
    rep["equity_curve_r"] = _equity(pooled, F)
    per, strat_rows = {}, []
    mid = ev["i"].median()
    run_id = f"bt-{symbol}-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:4]}"
    for name, g in ev.groupby("strategy"):
        po = pick_non_overlapping(g)
        st = _stats(po)
        h1, h2 = _stats(po[po["i"] <= mid]), _stats(po[po["i"] > mid])
        wins = po[po["y"] == 1]["bars"]
        hold = [float(np.percentile(wins, p)) for p in (25, 50, 75)] if len(wins) >= 10 else None
        halves_pos = bool((h1["ev_r"] or -1) > 0 and (h2["ev_r"] or -1) > 0 and h1["n"] >= 15 and h2["n"] >= 15)
        per[name] = {**st, "first_half": h1, "second_half": h2, "halves_positive": halves_pos,
                     "tp2_reach_rate": float(po["reach_tp2"].mean()), "tp3_reach_rate": float(po["reach_tp3"].mean()),
                     "hold_bars_win": hold}
        strat_rows.append(dict(symbol=symbol, strategy=name, computed_ts=int(time.time()), n=st["n"], win_rate=st["win_rate"],
                               wr_lo=st["wr_lo"], wr_hi=st["wr_hi"], ev_r=st["ev_r"], ev_lo=st["ev_lo"], pf=_fin(st["pf"]),
                               max_dd_r=st["max_dd_r"], run_id=run_id,
                               detail=clean({"halves_positive": halves_pos, "hold_bars_win": hold, "first_half": h1, "second_half": h2})))
    rep["per_strategy_rules_only"] = per
    if walk_forward_on and len(ev) >= 400:
        X = model_matrix(ev)
        rep["walk_forward"] = walk_forward(ev, X, s.horizon_bars, algos)
    elif walk_forward_on:
        rep["walk_forward"] = {"status": "INSUFFICIENT_EVENTS", "events": int(len(ev))}
    rep["status"] = "DONE"
    rep = clean(rep)
    with db.begin() as c:
        db.upsert(c, db.strategy_stats, [clean(r) for r in strat_rows], ["symbol", "strategy"])
        c.execute(db.backtest_runs.insert().values(id=run_id, symbol=symbol, created_ts=int(time.time()), kind="walk_forward" if walk_forward_on else "rules",
                                                     params={"horizon": s.horizon_bars, "tp1_r": s.tp1_r, "algos": list(algos)}, results=rep))
    rep["run_id"] = run_id
    return rep


def _fin(x):
    return None if x is None or (isinstance(x, float) and (np.isinf(x) or np.isnan(x))) else x


def latest_backtest(symbol: str) -> dict | None:
    from sqlalchemy import select
    t = db.backtest_runs
    with db.get_engine().connect() as c:
        r = c.execute(select(t).where(t.c.symbol == symbol).order_by(t.c.created_ts.desc()).limit(1)).first()
    return None if r is None else {**dict(r._mapping)}
