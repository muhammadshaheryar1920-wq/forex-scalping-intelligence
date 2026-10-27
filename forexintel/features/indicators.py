"""Causal technical indicators (value at bar t uses only bars <= t)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h, l, c, n: int = 14) -> pd.Series:
    return true_range(h, l, c).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(~((ad == 0) & (au > 0)), 100.0)
    return out.where(~((ad == 0) & (au == 0)), 50.0)


def adx(h, l, c, n: int = 14) -> pd.Series:
    up, dn = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    a = true_range(h, l, c).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd_hist(c: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> pd.Series:
    line = c.ewm(span=fast, adjust=False, min_periods=slow).mean() - c.ewm(span=slow, adjust=False, min_periods=slow).mean()
    return line - line.ewm(span=sig, adjust=False, min_periods=sig).mean()


def realized_vol(c: pd.Series, n: int = 20) -> pd.Series:
    return np.log(c).diff().rolling(n, min_periods=n).std()


def pct_rank(s: pd.Series, window: int = 500, min_periods: int = 100) -> pd.Series:
    """Causal percentile of s[t] within the PRECEDING `window` values (excludes the current bar)."""
    def f(x: np.ndarray) -> float:
        cur, past = x[-1], x[:-1]
        past = past[~np.isnan(past)]
        return np.nan if len(past) < 20 or np.isnan(cur) else float((past < cur).mean())
    return s.rolling(window + 1, min_periods=min_periods).apply(f, raw=True)
