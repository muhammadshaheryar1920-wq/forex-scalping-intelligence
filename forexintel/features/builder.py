"""Point-in-time feature matrix: M5 decision bars enriched with M15/H1 context from CLOSED higher-timeframe bars."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TF_SECONDS
from ..data.store import resample
from . import indicators as ind
from .sessions import asia_range, session_flags
from .structure import structure

FEATURE_VERSION = "f1"

M5_FEATURES = [
    "rsi", "atr_pct", "adx", "ema20_d", "ema50_d", "ema200_d", "ema20_slope", "ema50_slope", "macd_h", "rv20",
    "atr_rank", "body", "up_wick", "lo_wick", "range_atr", "ret1", "ret3", "ret6", "spread_atr",
    "trend", "since_bos", "bos_dir", "since_choch", "choch_dir", "since_sweep", "sweep_dir", "dist_sh", "dist_sl",
    "fvg_bull_d", "fvg_bear_d", "in_fvg_bull", "in_fvg_bear", "ob_bull_d", "ob_bear_d", "in_ob_bull", "in_ob_bear",
    "in_asia", "in_london", "in_ny", "overlap_ln", "tod_sin", "tod_cos", "asia_hi_d", "asia_lo_d",
]
M15_FEATURES = ["m15_trend", "m15_rsi", "m15_adx", "m15_atr_pct", "m15_ema20_d", "m15_ema50_d", "m15_ema50_slope",
                "m15_since_bos", "m15_bos_dir", "m15_dist_sh", "m15_dist_sl"]
H1_FEATURES = ["h1_trend", "h1_rsi", "h1_adx", "h1_atr_pct", "h1_ema50_d", "h1_ema200_d", "h1_ema50_slope",
               "h1_dist_sh", "h1_dist_sl"]
FEATURE_COLS = M5_FEATURES + M15_FEATURES + H1_FEATURES
PRICE_COLS = ["open", "high", "low", "close", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "atr", "spread", "has_bid_ask"]

WARMUP = 250


def _base(df: pd.DataFrame, k: int) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = ind.atr(h, l, c, 14)
    out = pd.DataFrame(index=df.index)
    out["atr"] = a
    out["atr_pct"] = a / c * 100
    out["rsi"] = ind.rsi(c, 14)
    out["adx"] = ind.adx(h, l, c, 14)
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    out["ema20_d"], out["ema50_d"], out["ema200_d"] = (c - e20) / a, (c - e50) / a, (c - e200) / a
    out["ema20_slope"], out["ema50_slope"] = (e20 - e20.shift(5)) / a, (e50 - e50.shift(5)) / a
    st = structure(o, h, l, c, a, k=k)
    st.index = df.index
    return pd.concat([out, st], axis=1), e20, e50, e200


def build_features(m5: pd.DataFrame) -> pd.DataFrame:
    """m5: canonical closed-bar frame. Returns feature frame indexed by M5 open time.

    Row t describes the market as known at the CLOSE of the M5 bar opening at t (decision time = t + 5 min).
    """
    o, h, l, c = m5["open"], m5["high"], m5["low"], m5["close"]
    base, _, _, _ = _base(m5, k=3)
    a = base["atr"]
    f = pd.DataFrame(index=m5.index)
    for col in PRICE_COLS[:-3]:
        f[col] = m5[col]
    f["atr"] = a
    f["spread"] = m5["ac"] - m5["bc"]
    f["has_bid_ask"] = m5["has_bid_ask"]
    rng = (h - l).replace(0, np.nan)
    f["spread_atr"] = f["spread"] / a
    f["body"] = (c - o).abs() / rng
    f["up_wick"] = (h - np.maximum(o, c)) / rng
    f["lo_wick"] = (np.minimum(o, c) - l) / rng
    f["range_atr"] = (h - l) / a
    for n in (1, 3, 6):
        f[f"ret{n}"] = (c - c.shift(n)) / a
    f["rv20"] = ind.realized_vol(c, 20) * 100
    f["macd_h"] = ind.macd_hist(c) / a
    f["atr_rank"] = ind.pct_rank(a / c, 500)
    for col in ("rsi", "atr_pct", "adx", "ema20_d", "ema50_d", "ema200_d", "ema20_slope", "ema50_slope", "trend",
                "since_bos", "bos_dir", "since_choch", "choch_dir", "since_sweep", "sweep_dir", "fvg_bull_d", "fvg_bear_d",
                "in_fvg_bull", "in_fvg_bear", "ob_bull_d", "ob_bear_d", "in_ob_bull", "in_ob_bear"):
        f[col] = base[col]
    for col in ("sh_level", "sl_level", "bos_up", "bos_dn", "choch_up", "choch_dn", "sweep_hi", "sweep_lo", "sweep_ext", "bos_level"):
        f[col] = base[col]
    f["dist_sh"] = (c - base["sh_level"]) / a
    f["dist_sl"] = (c - base["sl_level"]) / a
    f = pd.concat([f, session_flags(m5.index)], axis=1)
    ar = asia_range(m5.index, h, l)
    f["asia_hi"], f["asia_lo"] = ar["asia_hi"], ar["asia_lo"]
    f["asia_hi_d"], f["asia_lo_d"] = (c - ar["asia_hi"]) / a, (c - ar["asia_lo"]) / a
    f["ema20"], f["ema50"] = ind.ema(c, 20), ind.ema(c, 50)

    decision = (m5.index + pd.Timedelta(seconds=TF_SECONDS["M5"]))
    f["decision_ts"] = decision
    left = f.reset_index().rename(columns={"ts": "ts_open"})
    for tf, pre, k in (("M15", "m15_", 3), ("H1", "h1_", 2)):
        htf = resample(m5, tf)
        if htf.empty:
            continue
        b, e20, e50, e200 = _base(htf, k=k)
        blk = pd.DataFrame(index=htf.index)
        blk[pre + "trend"], blk[pre + "rsi"], blk[pre + "adx"], blk[pre + "atr_pct"] = b["trend"], b["rsi"], b["adx"], b["atr_pct"]
        blk[pre + "ema50_d"] = b["ema50_d"]
        blk[pre + "ema50_slope"] = b["ema50_slope"]
        blk[pre + "sh"], blk[pre + "sl"] = b["sh_level"], b["sl_level"]
        if tf == "M15":
            blk[pre + "ema20_d"] = b["ema20_d"]
            blk[pre + "since_bos"], blk[pre + "bos_dir"] = b["since_bos"], b["bos_dir"]
        else:
            blk[pre + "ema200_d"] = b["ema200_d"]
        blk["avail"] = blk.index + pd.Timedelta(seconds=TF_SECONDS[tf])
        right = blk.reset_index(drop=True).sort_values("avail")
        left = pd.merge_asof(left.sort_values("decision_ts"), right, left_on="decision_ts", right_on="avail",
                             direction="backward").drop(columns=["avail"])
        left[pre + "dist_sh"] = (left["close"] - left[pre + "sh"]) / left["atr"]
        left[pre + "dist_sl"] = (left["close"] - left[pre + "sl"]) / left["atr"]
    out = left.set_index("ts_open").sort_index()
    out.index.name = "ts"
    for col in FEATURE_COLS:
        if col not in out:
            out[col] = np.nan
    return out
