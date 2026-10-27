"""Candidate-event dataset: every strategy trigger + exact, cost-realistic label (TP1 before SL within horizon)."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from ..config import Settings, get_settings
from ..features.builder import FEATURE_COLS, build_features
from ..strategy import strategies as S
from ..strategy.plan import make_plan
from ..strategy.regime import classify
from .simulate import Arrays, simulate

SIGNED = ["trend", "bos_dir", "choch_dir", "sweep_dir", "m15_trend", "m15_bos_dir", "h1_trend", "ret1", "ret3", "ret6",
          "ema20_d", "ema50_d", "ema200_d", "ema20_slope", "ema50_slope", "macd_h", "m15_ema20_d", "m15_ema50_d",
          "m15_ema50_slope", "h1_ema50_d", "h1_ema200_d", "h1_ema50_slope"]
RSI_COLS = ["rsi", "m15_rsi", "h1_rsi"]
SWAPS = [("up_wick", "lo_wick"), ("fvg_bull_d", "fvg_bear_d"), ("in_fvg_bull", "in_fvg_bear"), ("ob_bull_d", "ob_bear_d"),
         ("in_ob_bull", "in_ob_bear")]
NEG_SWAPS = [("dist_sh", "dist_sl"), ("m15_dist_sh", "m15_dist_sl"), ("h1_dist_sh", "h1_dist_sl"), ("asia_hi_d", "asia_lo_d")]
META = ["i", "strategy", "side", "entry_ref", "sl", "tp1", "tp2", "tp3", "risk", "risk_atr", "cost_r", "base_regime", "vol_state"]
LABELS = ["y", "net_r", "outcome", "bars", "reach_tp2", "reach_tp3"]


def prepare(m5: pd.DataFrame) -> pd.DataFrame:
    F = build_features(m5)
    return F.join(classify(F))


def valid_mask(F: pd.DataFrame) -> pd.Series:
    return F["h1_ema200_d"].notna() & F["atr_rank"].notna() & F["atr"].notna() & F["spread"].notna()


def event_row(F: pd.DataFrame, i: int, strategy: str, side: int, plan: dict) -> dict:
    row = F.iloc[i]
    d = {"i": i, "ts": F.index[i], "strategy": strategy, "side": side, "entry_ref": plan["entry_ref"], "sl": plan["sl"],
         "tp1": plan["tp1"], "tp2": plan["tp2"], "tp3": plan["tp3"], "risk": plan["risk"], "risk_atr": plan["risk_atr"],
         "cost_r": plan["cost_r"], "base_regime": row["base_regime"], "vol_state": row["vol_state"]}
    for c in FEATURE_COLS:
        d[c] = row[c]
    return d


def build_events(F: pd.DataFrame, symbol: str, s: Settings | None = None, names=None, label: bool = True) -> tuple[pd.DataFrame, dict]:
    s = s or get_settings()
    A = Arrays.from_frame(F)
    ok = valid_mask(F).to_numpy()
    gens = S.generate(F, names)
    rows, stats = [], {"triggers": 0, "plan_rejected": 0, "nofill": 0, "no_future": 0}
    for name, g in gens.items():
        for side, col, anc in ((1, "long", "long_anchor"), (-1, "short", "short_anchor")):
            idxs = np.flatnonzero(g[col].to_numpy() & ok)
            anchors = g[anc].to_numpy()
            for i in idxs:
                stats["triggers"] += 1
                plan = make_plan(F.iloc[i], side, anchors[i], s, symbol)
                if not plan["ok"]:
                    stats["plan_rejected"] += 1
                    continue
                ev = event_row(F, int(i), name, side, plan)
                if label:
                    sim = simulate(A, int(i), plan, symbol, s)
                    if sim is None:
                        stats["no_future"] += 1
                        continue
                    if sim["outcome"] == "NOFILL":
                        stats["nofill"] += 1
                        continue
                    ev.update({"y": sim["y"], "net_r": sim["net_r"], "outcome": sim["outcome"], "bars": sim["bars"],
                               "reach_tp2": sim["reach_tp2"], "reach_tp3": sim["reach_tp3"]})
                rows.append(ev)
    ev = pd.DataFrame(rows)
    if len(ev):
        ev = ev.sort_values(["i", "strategy"]).reset_index(drop=True)
    return ev, stats


def orient(X: pd.DataFrame, side: np.ndarray) -> pd.DataFrame:
    """Express features from the trade's own perspective so long and short samples share one model."""
    X = X.copy()
    sh = np.asarray(side) == -1
    if sh.any():
        for c in SIGNED:
            if c in X:
                X.loc[sh, c] = -X.loc[sh, c]
        for c in RSI_COLS:
            if c in X:
                X.loc[sh, c] = 100 - X.loc[sh, c]
        for a, b in SWAPS:
            va, vb = X.loc[sh, a].to_numpy().copy(), X.loc[sh, b].to_numpy().copy()
            X.loc[sh, a], X.loc[sh, b] = vb, va
        for a, b in NEG_SWAPS:
            va, vb = X.loc[sh, a].to_numpy().copy(), X.loc[sh, b].to_numpy().copy()
            X.loc[sh, a], X.loc[sh, b] = -vb, -va
    return X


def model_matrix(ev: pd.DataFrame) -> pd.DataFrame:
    X = ev[FEATURE_COLS].astype(float).copy()
    X = orient(X, ev["side"].to_numpy())
    for st in S.STRATEGIES:
        X[f"st_{st}"] = (ev["strategy"] == st).astype(float).to_numpy()
    side = ev["side"].to_numpy()
    base = ev["base_regime"].to_numpy()
    X["reg_with_trend"] = (((base == "TREND_UP") & (side == 1)) | ((base == "TREND_DOWN") & (side == -1))).astype(float)
    X["reg_counter"] = (((base == "TREND_UP") & (side == -1)) | ((base == "TREND_DOWN") & (side == 1))).astype(float)
    X["reg_range"] = (base == "RANGE").astype(float)
    X["vol_high"] = (ev["vol_state"].to_numpy() == "HIGH").astype(float)
    X["vol_quiet"] = (ev["vol_state"].to_numpy() == "QUIET").astype(float)
    X["risk_atr"] = ev["risk_atr"].astype(float).to_numpy()
    X["cost_r"] = ev["cost_r"].astype(float).to_numpy()
    return X


def direction_features(F: pd.DataFrame) -> pd.DataFrame:
    """Feature matrix for the directional (UP/DOWN) models: raw features + regime flags, one row per M5 bar."""
    X = F[FEATURE_COLS].astype(float).copy()
    X["reg_up"] = (F["base_regime"] == "TREND_UP").astype(float)
    X["reg_down"] = (F["base_regime"] == "TREND_DOWN").astype(float)
    X["reg_range"] = (F["base_regime"] == "RANGE").astype(float)
    X["vol_high"] = (F["vol_state"] == "HIGH").astype(float)
    X["vol_quiet"] = (F["vol_state"] == "QUIET").astype(float)
    return X


def pick_non_overlapping(ev: pd.DataFrame) -> pd.DataFrame:
    """One open position at a time: earliest trigger wins, others skipped until it exits."""
    keep, busy = [], -1
    for k, r in ev.sort_values(["i", "strategy"]).iterrows():
        if r["i"] > busy:
            keep.append(k)
            busy = r["i"] + int(r["bars"])
    return ev.loc[keep]


def dataset_hash(m5: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(f"{len(m5)}|{m5.index[0]}|{m5.index[-1]}".encode())
    h.update(np.ascontiguousarray(m5[["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]].to_numpy(dtype="float64")).tobytes())
    return h.hexdigest()[:16]
