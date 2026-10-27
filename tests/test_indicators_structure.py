import numpy as np
import pandas as pd

from forexintel.features import indicators as ind
from forexintel.features.structure import structure


def ohlc_from_close(c, wick=0.3):
    c = np.asarray(c, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return o, np.maximum(o, c) + wick, np.minimum(o, c) - wick, c


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1.0, 80.0))
    dn = pd.Series(np.arange(80.0, 1.0, -1.0))
    assert ind.rsi(up).dropna().iloc[-1] == 100.0
    assert ind.rsi(dn).dropna().iloc[-1] < 1.0
    r = ind.rsi(pd.Series(np.random.default_rng(1).normal(0, 1, 500).cumsum() + 100)).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_and_adx_sane():
    rng = np.random.default_rng(2)
    c = pd.Series(100 + rng.normal(0, 1, 400).cumsum())
    h, l = c + 0.5, c - 0.5
    a = ind.atr(h, l, c).dropna()
    assert (a > 0).all()
    adx = ind.adx(h, l, c).dropna()
    assert ((adx >= 0) & (adx <= 100)).all()
    trend = pd.Series(np.arange(400, dtype=float))
    assert ind.adx(trend + 0.5, trend - 0.5, trend).dropna().iloc[-1] > 40


def test_pct_rank_is_causal():
    s = pd.Series(np.random.default_rng(3).normal(size=900))
    full = ind.pct_rank(s, 200, 50)
    part = ind.pct_rank(s.iloc[:600], 200, 50)
    assert np.allclose(full.iloc[:600].fillna(-1), part.fillna(-1))


def test_swing_confirmation_delay_and_bos():
    # pivot high at index 5 can only be KNOWN from index 7 (k=2)
    c = np.array([1, 2, 3, 4, 5, 8, 5, 4, 5, 6, 9, 10, 11], dtype=float)
    o, h, l, cl = ohlc_from_close(c, 0.1)
    st = structure(o, h, l, cl, np.ones_like(c), k=2)
    assert np.isnan(st["sh_level"].iloc[6])
    assert st["sh_level"].iloc[7] == h[5]
    assert st["bos_up"].any()


def test_trend_bos_choch_sequence():
    t = np.arange(120)
    c = np.where(t < 70, 100 + 0.18 * t + 1.6 * np.sin(2 * np.pi * t / 10), 112.6 - 0.9 * (t - 70) + 1.6 * np.sin(2 * np.pi * t / 10))
    o, h, l, cl = ohlc_from_close(c)
    st = structure(o, h, l, cl, np.full(len(c), 1.0), k=2)
    assert st["trend"].iloc[60] == 1
    assert st["bos_up"].sum() >= 1
    assert st["choch_dn"].sum() >= 1
    assert st["trend"].iloc[-1] == -1


def test_structure_is_causal():
    rng = np.random.default_rng(4)
    c = 100 + rng.normal(0, 1, 400).cumsum()
    o, h, l, cl = ohlc_from_close(c)
    a = np.full(400, 1.5)
    full = structure(o, h, l, cl, a, k=3)
    for T in (150, 260, 399):
        part = structure(o[:T + 1], h[:T + 1], l[:T + 1], cl[:T + 1], a[:T + 1], k=3)
        for col in ("trend", "sh_level", "sl_level", "bos_up", "choch_dn", "sweep_hi", "fvg_bull_d", "since_bos"):
            x, y = full[col].iloc[T], part[col].iloc[T]
            assert (pd.isna(x) and pd.isna(y)) or x == y, (col, T)


def test_liquidity_sweep_and_fvg():
    # confirmed swing high at 10 (index 5); bar 10 wicks above it but closes back inside
    c = [5, 6, 7, 8, 9.5, 9.9, 9.0, 8.5, 8.4, 8.6, 9.2, 9.0]
    o, h, l, cl = ohlc_from_close(c, 0.05)
    h[10] = 10.6
    st = structure(o, h, l, cl, np.ones(len(c)), k=2)
    assert st["sweep_hi"].iloc[10]
    assert st["sweep_dir"].iloc[10] == -1
    # bullish FVG: candle0 high 100, candle2 low 101 => gap [100, 101]
    o2 = np.array([99, 100, 101.5, 102, 102.5]); h2 = np.array([100, 102, 103, 103, 103.5]); l2 = np.array([98, 99.5, 101, 101.5, 102])
    c2 = np.array([99.8, 101.8, 102.5, 102.5, 103])
    st2 = structure(o2, h2, l2, c2, np.ones(5), k=2)
    assert not np.isnan(st2["fvg_bull_d"].iloc[2])
