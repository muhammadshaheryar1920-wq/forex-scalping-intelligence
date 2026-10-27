"""Independent, regime-aware setup generators. Each returns per-bar long/short triggers and SL anchors.

No strategy requires RSI/MACD/EMA/SMC to all agree: indicators are *features* for the ranker, not gates.
Every trigger uses only information available at the close of the decision bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TREND_CONTINUATION = "TREND_CONTINUATION"
BREAKOUT_RETEST = "BREAKOUT_RETEST"
SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
RANGE_MEAN_REVERSION = "RANGE_MEAN_REVERSION"
STRATEGIES = (TREND_CONTINUATION, BREAKOUT_RETEST, SWEEP_REVERSAL, RANGE_MEAN_REVERSION)


def _out(F: pd.DataFrame, long: pd.Series, short: pd.Series, la: pd.Series, sa: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"long": long.fillna(False).astype(bool), "short": short.fillna(False).astype(bool),
                         "long_anchor": la, "short_anchor": sa}, index=F.index)


def trend_continuation(F: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o, a = F["close"], F["high"], F["low"], F["open"], F["atr"]
    up, dn = F["base_regime"] == "TREND_UP", F["base_regime"] == "TREND_DOWN"
    active = F["vol_state"] != "QUIET"
    pull_long = ((l - F["ema20"]) / a).rolling(10, min_periods=10).min() <= 0.2
    pull_short = ((h - F["ema20"]) / a).rolling(10, min_periods=10).max() >= -0.2
    long = up & active & pull_long & (c > h.shift(1)) & (c > F["ema20"]) & (c > o) & (F["ema50_d"] < 3.0)
    short = dn & active & pull_short & (c < l.shift(1)) & (c < F["ema20"]) & (c < o) & (F["ema50_d"] > -3.0)
    return _out(F, long, short, l.rolling(8, min_periods=1).min(), h.rolling(8, min_periods=1).max())


def breakout_retest(F: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o, a = F["close"], F["high"], F["low"], F["open"], F["atr"]
    lvl = F["bos_level"]
    bos_id = (F["bos_up"] | F["bos_dn"]).cumsum()
    window = (F["since_bos"] >= 2) & (F["since_bos"] <= 20) & lvl.notna() & (F["vol_state"] != "QUIET")
    held_up = c.groupby(bos_id).cummin() > lvl - 0.3 * a        # no close back through the level since the break bar
    held_dn = c.groupby(bos_id).cummax() < lvl + 0.3 * a
    long_c = window & (F["bos_dir"] == 1) & (l <= lvl + 0.25 * a) & (c > lvl) & (c > o) & held_up
    short_c = window & (F["bos_dir"] == -1) & (h >= lvl - 0.25 * a) & (c < lvl) & (c < o) & held_dn
    long = long_c & (long_c.astype(int).groupby(bos_id).cumsum() == 1)
    short = short_c & (short_c.astype(int).groupby(bos_id).cumsum() == 1)
    return _out(F, long, short, np.minimum(l, lvl), np.maximum(h, lvl))


def sweep_reversal(F: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o = F["close"], F["high"], F["low"], F["open"]
    sweep_id = (F["sweep_hi"] | F["sweep_lo"]).cumsum()
    win = (F["since_sweep"] >= 1) & (F["since_sweep"] <= 3)
    long_c = win & (F["sweep_dir"] == 1) & (c > o) & (c > h.shift(1)) & (F["base_regime"] != "TREND_DOWN")
    short_c = win & (F["sweep_dir"] == -1) & (c < o) & (c < l.shift(1)) & (F["base_regime"] != "TREND_UP")
    long = long_c & (long_c.astype(int).groupby(sweep_id).cumsum() == 1)
    short = short_c & (short_c.astype(int).groupby(sweep_id).cumsum() == 1)
    return _out(F, long, short, F["sweep_ext"], F["sweep_ext"])


def range_mean_reversion(F: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o, a = F["close"], F["high"], F["low"], F["open"], F["atr"]
    hi = h.rolling(48, min_periods=48).max().shift(1)
    lo = l.rolling(48, min_periods=48).min().shift(1)
    width = hi - lo
    w_atr = width / a
    pos = (c - lo) / width.replace(0, np.nan)
    ok = (F["base_regime"] == "RANGE") & (F["vol_state"] == "NORMAL") & (w_atr >= 2.5) & (w_atr <= 9.0)
    long = ok & (pos < 0.2) & (c > o) & (F["lo_wick"] >= 0.3)
    short = ok & (pos > 0.8) & (c < o) & (F["up_wick"] >= 0.3)
    return _out(F, long, short, np.minimum(l, lo), np.maximum(h, hi))


GENERATORS = {
    TREND_CONTINUATION: trend_continuation,
    BREAKOUT_RETEST: breakout_retest,
    SWEEP_REVERSAL: sweep_reversal,
    RANGE_MEAN_REVERSION: range_mean_reversion,
}


def generate(F: pd.DataFrame, names=None) -> dict[str, pd.DataFrame]:
    """F must already contain regime columns (see regime.classify)."""
    return {n: GENERATORS[n](F) for n in (names or STRATEGIES)}


def explain(strategy: str, row: pd.Series, side: int) -> list[str]:
    """Human-readable reasons for a trigger row (supports the signal's market-structure reasoning)."""
    d = "bullish" if side == 1 else "bearish"
    r = [f"regime {row.get('regime', '?')} (M15 ADX {row.get('m15_adx', float('nan')):.0f}, H1 trend {int(row.get('h1_trend', 0) or 0):+d})"]
    if strategy == TREND_CONTINUATION:
        r.append(f"H1/M15 {d} bias, M5 pulled back to EMA20 then re-accelerated through the prior bar's extreme")
    elif strategy == BREAKOUT_RETEST:
        r.append(f"M5 break of structure ({row.get('since_bos', 0):.0f} bars ago) retested from the {'above' if side == 1 else 'below'} and held")
    elif strategy == SWEEP_REVERSAL:
        r.append(f"liquidity sweep of a confirmed swing {'low' if side == 1 else 'high'} ({row.get('since_sweep', 0):.0f} bars ago), closed back inside, follow-through {d}")
    elif strategy == RANGE_MEAN_REVERSION:
        r.append(f"range-bound market, rejection at the range {'low' if side == 1 else 'high'} with {d} close")
    if row.get("in_fvg_bull" if side == 1 else "in_fvg_bear", 0):
        r.append("price is inside an unmitigated fair value gap in the trade direction")
    if row.get("in_ob_bull" if side == 1 else "in_ob_bear", 0):
        r.append("price is reacting from an order-block zone in the trade direction")
    return r
