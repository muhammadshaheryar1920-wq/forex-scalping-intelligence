"""Training pipeline: chronological split with purge/embargo, model comparison on validation only,
single look at the untouched test segment, calibration, ablation, versioned registration."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from ..config import Settings, get_settings, normalize_symbol
from ..data import quality, store
from ..features.builder import FEATURE_COLS
from . import registry
from .dataset import (build_events, dataset_hash, direction_features, model_matrix, pick_non_overlapping, prepare,
                      valid_mask)
from .metrics import clean, summarize
from .models import ALGOS, Bundle, Platt, fit_select, make_model, prob_metrics, reliability

MIN_EVENTS = 300
EMBARGO = 6
CUTS = (0.55, 0.70, 0.85)

GROUPS = {
    "indicators": ["rsi", "atr_pct", "adx", "ema20_d", "ema50_d", "ema200_d", "ema20_slope", "ema50_slope", "macd_h",
                   "rv20", "atr_rank", "spread_atr"],
    "price_action": ["body", "up_wick", "lo_wick", "range_atr", "ret1", "ret3", "ret6"],
    "structure": ["trend", "since_bos", "bos_dir", "since_choch", "choch_dir", "since_sweep", "sweep_dir", "dist_sh", "dist_sl"],
    "smc_zones": ["fvg_bull_d", "fvg_bear_d", "in_fvg_bull", "in_fvg_bear", "ob_bull_d", "ob_bear_d", "in_ob_bull", "in_ob_bear"],
    "sessions": ["in_asia", "in_london", "in_ny", "overlap_ln", "tod_sin", "tod_cos", "asia_hi_d", "asia_lo_d"],
    "htf_context": [c for c in FEATURE_COLS if c.startswith(("m15_", "h1_"))],
}


def split_events(ev: pd.DataFrame, H: int) -> dict[str, np.ndarray]:
    """train/cal/threshold/test by time with purge: no earlier-segment label window overlaps a later segment."""
    i = ev["i"].to_numpy()
    n = len(ev)
    b = [i[int(n * c)] for c in CUTS]
    gap = H + 2 + EMBARGO
    return {
        "train": i < b[0] - gap, "cal": (i >= b[0]) & (i < b[1] - gap),
        "th": (i >= b[1]) & (i < b[2] - gap), "test": i >= b[2],
        "bounds": np.array(b),
    }


def _sel_stats(ev_sel: pd.DataFrame) -> dict:
    po = pick_non_overlapping(ev_sel) if len(ev_sel) else ev_sel
    return summarize(po["net_r"].to_numpy(), po["y"].to_numpy()) if len(po) else summarize([])


def ablation(ev, X, masks, algo: str, cols: list, seed: int = 7) -> list[dict]:
    """Drop one feature group at a time; report change in threshold-set AUC/logloss (informational, never used to pick on test)."""
    y = ev["y"].to_numpy()
    out = []
    for name, group in {"(all features)": [], **GROUPS}.items():
        use = [c for c in cols if c not in group]
        if not use:
            continue
        m = make_model(algo, seed)
        m.fit(X.loc[masks["train"], use], y[masks["train"]])
        cal = Platt().fit(m.predict_proba(X.loc[masks["cal"], use])[:, 1], y[masks["cal"]])
        p_th = cal.predict(m.predict_proba(X.loc[masks["th"], use])[:, 1])
        met = prob_metrics(y[masks["th"]], p_th)
        out.append({"group_removed": name, "n_features": len(use), "val_auc": met.get("auc"), "val_logloss": met.get("logloss")})
    base = next((o for o in out if o["group_removed"] == "(all features)"), None)
    if base and base["val_auc"] is not None:
        for o in out:
            if o["val_auc"] is not None:
                o["d_auc"] = o["val_auc"] - base["val_auc"]
    return out


def train_trade_model(symbol: str, algos=ALGOS, s: Settings | None = None, seed: int = 7) -> dict:
    s = s or get_settings()
    symbol = normalize_symbol(symbol)
    m5, meta = store.load_series(symbol)
    rep: dict = {"symbol": symbol, "kind": "trade", "source": meta, "bars": int(len(m5)), "status": "NOT_TRAINED"}
    if len(m5) < quality.MIN_BARS:
        rep["reason"] = f"only {len(m5)} bars (< {quality.MIN_BARS}); run sync first"
        return rep
    F = prepare(m5)
    ev, stats = build_events(F, symbol, s)
    rep.update(event_stats=stats, events=int(len(ev)), data_from=m5.index[0].isoformat(), data_to=m5.index[-1].isoformat(),
               dataset_hash=dataset_hash(m5), assumed_spread_bars=meta.get("assumed_spread_bars"))
    if len(ev) < MIN_EVENTS:
        rep["reason"] = f"only {len(ev)} candidate events (< {MIN_EVENTS}); more history needed"
        return rep
    X = model_matrix(ev)
    cols = list(X.columns)
    y, r = ev["y"].to_numpy(), ev["net_r"].to_numpy()
    masks = split_events(ev, s.horizon_bars)
    rep["split_sizes"] = {k: int(masks[k].sum()) for k in ("train", "cal", "th", "test")}
    rep["base_rate_train"] = float(y[masks["train"]].mean())
    results, bundles = {}, {}
    for algo in algos:
        try:
            b = fit_select(X[masks["train"]], y[masks["train"]], r[masks["train"]], X[masks["cal"]], y[masks["cal"]],
                           X[masks["th"]], r[masks["th"]], y[masks["th"]], algo, cols, seed)
        except Exception as e:  # noqa: BLE001
            results[algo] = {"error": f"{type(e).__name__}: {e}"}
            continue
        bundles[algo] = b
        p_th = b.proba(X[masks["th"]])
        results[algo] = {"threshold": b.threshold, "val_selection": b.meta["threshold_selection"],
                         "val_metrics": prob_metrics(y[masks["th"]], p_th), "rwin": b.rwin, "rnon": b.rnon}
    rep["algos"] = results
    ok = {a: b for a, b in bundles.items() if b.threshold is not None}
    test_ev = ev[masks["test"]]
    rep["baseline_test"] = {"pooled_rules_only": _sel_stats(test_ev),
                            "by_strategy": {k: summarize(g["net_r"].to_numpy(), g["y"].to_numpy())
                                            for k, g in test_ev.groupby("strategy")}}
    rep["baseline_val"] = {"pooled_rules_only": _sel_stats(ev[masks["th"]])}
    if not ok:
        rep.update(status="REJECTED", reason="no model found a positive-expectancy threshold on the validation slice "
                   "(model abstains; rules-only baseline stays, and the baseline itself has no validated edge)")
        chosen = max(bundles.values(), key=lambda b: (prob_metrics(y[masks["th"]], b.proba(X[masks["th"]])).get("auc") or 0),
                     default=None)
    else:
        chosen = max(ok.values(), key=lambda b: b.meta["threshold_selection"]["best_ev"])
    if chosen is None:
        rep["reason"] = rep.get("reason", "no model could be fitted")
        return rep
    # ---- single evaluation on the untouched test segment (model + threshold frozen above) ----
    p_te = chosen.proba(X[masks["test"]])
    rep["chosen"] = chosen.algo
    rep["test_prob_metrics"] = prob_metrics(y[masks["test"]], p_te)
    rep["test_reliability"] = reliability(y[masks["test"]], p_te)
    if chosen.threshold is not None:
        sel = test_ev[p_te >= chosen.threshold]
        rep["test_selected"] = _sel_stats(sel)
        base_ev = rep["baseline_test"]["pooled_rules_only"]["ev_r"]
        sel_s = rep["test_selected"]
        beats = (sel_s["n"] >= 30 and sel_s["ev_r"] is not None and sel_s["ev_r"] > 0 and
                 (base_ev is None or sel_s["ev_r"] > base_ev))
        rep["significant"] = bool(beats and sel_s["ev_lo"] is not None and sel_s["ev_lo"] > 0)
        if beats:
            rep["status"] = "CANDIDATE"
            rep["note"] = ("expectancy CI excludes zero" if rep["significant"] else
                           "positive but NOT statistically significant; needs forward paper evidence")
        else:
            rep["status"] = "REJECTED"
            rep.setdefault("reason", "frozen model did not beat the rules-only baseline with positive net expectancy on untouched test")
    for algo, b in bundles.items():            # informational: other models on test (never used for selection)
        if algo != chosen.algo:
            p = b.proba(X[masks["test"]])
            rep["algos"][algo]["test_prob_metrics"] = prob_metrics(y[masks["test"]], p)
    rep["ablation"] = ablation(ev, X, masks, chosen.algo, cols, seed)
    bounds_ts = [F.index[int(b)].isoformat() for b in masks["bounds"]]
    rep["split_bounds_ts"] = bounds_ts
    train_end_ts = int(F.index[int(masks["bounds"][2])].timestamp())
    chosen.meta.update(horizon=s.horizon_bars, tp1_r=s.tp1_r, split_bounds=bounds_ts, trained_on_provider=meta["history_provider"])
    version = registry.save_bundle(chosen, symbol, "trade", rep["dataset_hash"], train_end_ts, clean(rep),
                                   rep["status"], extra={"label": "TP1 before SL within horizon after costs"})
    rep["version"] = version
    return clean(rep)


def train_direction_model(symbol: str, horizons=(3, 6, 12), s: Settings | None = None, seed: int = 7) -> dict:
    s = s or get_settings()
    symbol = normalize_symbol(symbol)
    m5, meta = store.load_series(symbol)
    rep = {"symbol": symbol, "kind": "direction", "horizons": {}, "status": "NOT_TRAINED"}
    if len(m5) < quality.MIN_BARS:
        rep["reason"] = f"only {len(m5)} bars"
        return rep
    F = prepare(m5)
    ok = valid_mask(F)
    X_all = direction_features(F)
    cols = list(X_all.columns)
    dh = dataset_hash(m5)
    best_version = {}
    for h in horizons:
        fwd = F["close"].shift(-h) - F["close"]
        keep = ok & fwd.notna() & (fwd.abs() >= 0.15 * F["atr"])
        X, y = X_all[keep], (fwd[keep] > 0).astype(int).to_numpy()
        n = len(X)
        if n < 1500:
            rep["horizons"][h] = {"status": "INSUFFICIENT_DATA", "n": n}
            continue
        gap = h + EMBARGO
        a, b = int(n * 0.6), int(n * 0.8)
        tr, va, te = slice(0, a - gap), slice(a, b - gap), slice(b, n)
        res = {}
        for algo in ("logistic", "lightgbm"):
            m = make_model(algo, seed)
            m.fit(X.iloc[tr][cols], y[tr])
            cal = Platt().fit(m.predict_proba(X.iloc[va][cols])[:, 1], y[va])
            pv = cal.predict(m.predict_proba(X.iloc[va][cols])[:, 1])
            res[algo] = (m, cal, prob_metrics(y[va], pv))
        pick = min(res, key=lambda k: res[k][2].get("logloss", 9))
        m, cal, vm = res[pick]
        pt = cal.predict(m.predict_proba(X.iloc[te][cols])[:, 1])
        yt = y[te]
        pred = (pt >= 0.5).astype(int)
        tpr = float((pred[yt == 1] == 1).mean()) if (yt == 1).any() else float("nan")
        tnr = float((pred[yt == 0] == 0).mean()) if (yt == 0).any() else float("nan")
        bal = (tpr + tnr) / 2
        se = 0.5 / np.sqrt(max(1.0, len(yt) / h))      # overlap-aware: h-bar forward labels are serially dependent
        pm = prob_metrics(yt, pt)
        base_ll = float(-np.mean(yt * np.log(yt.mean() + 1e-9) + (1 - yt) * np.log(1 - yt.mean() + 1e-9)))
        passes = bool(bal - 1.96 * se > 0.5 and pm.get("logloss", 9) < base_ll)
        rep["horizons"][h] = {"algo": pick, "n": n, "val": vm, "test": pm, "balanced_accuracy": bal, "ba_lo": bal - 1.96 * se,
                              "baseline_logloss": base_ll, "passes": passes, "reliability": reliability(yt, pt)}
        bundle = Bundle(pick, m, cal, cols, 0.5, 0.0, 0.0, {"horizon": h})
        status = "CANDIDATE" if passes else "REJECTED"
        v = registry.save_bundle(bundle, symbol, f"direction_h{h}", dh, int(X.index[b - 1].timestamp()), clean(rep["horizons"][h]),
                                 status, extra={"horizon": h})
        rep["horizons"][h]["version"] = v
        best_version[h] = v
    rep["status"] = "DONE"
    return clean(rep)


def train_symbol(symbol: str, algos=ALGOS, directions: bool = True) -> dict:
    t = time.time()
    out = {"trade": train_trade_model(symbol, algos)}
    if directions:
        out["direction"] = train_direction_model(symbol)
    out["seconds"] = round(time.time() - t, 1)
    return out
