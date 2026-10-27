"""Deterministic, causal market-structure / Smart-Money-Concept detection.

All definitions are mechanical (see docs/algorithm-rules.md). Every output at bar t uses only bars <= t:
a swing pivot with window k is *confirmed* k bars after it forms, never earlier.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CAP = 200


def structure(open_, high, low, close, atr, k: int = 3, sweep_tol: float = 0.05, fvg_min: float = 0.2,
              ob_disp: float = 1.2, max_zone_age: int = 288) -> pd.DataFrame:
    o, h, l, c, a = (np.asarray(x, dtype=float) for x in (open_, high, low, close, atr))
    n = len(c)
    nan = np.nan
    trend = np.zeros(n)
    sh_lvl, sl_lvl = np.full(n, nan), np.full(n, nan)
    bos_up, bos_dn, choch_up, choch_dn = (np.zeros(n, bool) for _ in range(4))
    since_bos, since_choch, since_sweep = (np.full(n, float(CAP)) for _ in range(3))
    bos_dir, choch_dir, sweep_dir = (np.zeros(n) for _ in range(3))
    bos_lvl, sweep_ext = np.full(n, nan), np.full(n, nan)
    sweep_hi_ev, sweep_lo_ev = np.zeros(n, bool), np.zeros(n, bool)
    fvg_b_d, fvg_s_d, ob_b_d, ob_s_d = (np.full(n, nan) for _ in range(4))
    in_fvg_b, in_fvg_s, in_ob_b, in_ob_s = (np.zeros(n) for _ in range(4))

    sh = sl = psh = psl = nan
    sh_broken = sl_broken = False
    tr = 0
    lb_i, lb_d, lb_l = -10**9, 0, nan
    lc_i, lc_d = -10**9, 0
    ls_i, ls_d, ls_e = -10**9, 0, nan
    fvg_b, fvg_s, ob_b, ob_s = [], [], [], []      # zones: [lo, hi, created_idx]

    for t in range(n):
        i = t - k
        if i - k >= 0:
            if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:t + 1].max():
                psh, sh, sh_broken = sh, h[i], False
                if not np.isnan(psh) and not np.isnan(psl) and not np.isnan(sl):
                    if sh > psh and sl > psl:
                        tr = 1
                    elif sh < psh and sl < psl:
                        tr = -1
            if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:t + 1].min():
                psl, sl, sl_broken = sl, l[i], False
                if not np.isnan(psh) and not np.isnan(psl) and not np.isnan(sh):
                    if sh > psh and sl > psl:
                        tr = 1
                    elif sh < psh and sl < psl:
                        tr = -1
        at = a[t]
        brk_up = brk_dn = False
        if not np.isnan(sh) and not sh_broken and c[t] > sh:
            sh_broken, brk_up = True, True
            if tr == -1:
                choch_up[t] = True
                lc_i, lc_d = t, 1
            else:
                bos_up[t] = True
                lb_i, lb_d, lb_l = t, 1, sh
            tr = 1
        elif not np.isnan(sl) and not sl_broken and c[t] < sl:
            sl_broken, brk_dn = True, True
            if tr == 1:
                choch_dn[t] = True
                lc_i, lc_d = t, -1
            else:
                bos_dn[t] = True
                lb_i, lb_d, lb_l = t, -1, sl
            tr = -1
        if not np.isnan(at) and at > 0:
            # liquidity sweep: wick pierces a confirmed swing level, bar closes back inside
            if not np.isnan(sh) and not sh_broken and h[t] > sh + sweep_tol * at and c[t] < sh:
                sweep_hi_ev[t] = True
                ls_i, ls_d, ls_e = t, -1, h[t]
            if not np.isnan(sl) and not sl_broken and l[t] < sl - sweep_tol * at and c[t] > sl:
                sweep_lo_ev[t] = True
                ls_i, ls_d, ls_e = t, 1, l[t]
            # fair value gaps (3-candle imbalance, known at close of bar t)
            if t >= 2:
                if l[t] > h[t - 2] and (l[t] - h[t - 2]) >= fvg_min * at:
                    fvg_b.append([h[t - 2], l[t], t])
                if h[t] < l[t - 2] and (l[t - 2] - h[t]) >= fvg_min * at:
                    fvg_s.append([h[t], l[t - 2], t])
            # order blocks: last opposite candle before a displacement that breaks structure
            if brk_up or brk_dn:
                for j in range(t - 1, max(-1, t - 11), -1):
                    if brk_up and c[j] < o[j] and (c[t] - l[j]) >= ob_disp * at:
                        ob_b.append([l[j], h[j], t])
                        break
                    if brk_dn and c[j] > o[j] and (h[j] - c[t]) >= ob_disp * at:
                        ob_s.append([l[j], h[j], t])
                        break
        # zone maintenance
        fvg_b = [z for z in fvg_b if c[t] >= z[0] and t - z[2] <= max_zone_age][-10:]
        fvg_s = [z for z in fvg_s if c[t] <= z[1] and t - z[2] <= max_zone_age][-10:]
        ob_b = [z for z in ob_b if c[t] >= z[0] and t - z[2] <= max_zone_age][-6:]
        ob_s = [z for z in ob_s if c[t] <= z[1] and t - z[2] <= max_zone_age][-6:]
        if not np.isnan(at) and at > 0:
            if fvg_b:
                d = [(c[t] - z[1]) / at for z in fvg_b]
                fvg_b_d[t] = d[int(np.argmin(np.abs(d)))]
                in_fvg_b[t] = float(any(z[0] <= c[t] <= z[1] for z in fvg_b))
            if fvg_s:
                d = [(z[0] - c[t]) / at for z in fvg_s]
                fvg_s_d[t] = d[int(np.argmin(np.abs(d)))]
                in_fvg_s[t] = float(any(z[0] <= c[t] <= z[1] for z in fvg_s))
            if ob_b:
                d = [(c[t] - z[1]) / at for z in ob_b]
                ob_b_d[t] = d[int(np.argmin(np.abs(d)))]
                in_ob_b[t] = float(any(l[t] <= z[1] and c[t] >= z[0] for z in ob_b))
            if ob_s:
                d = [(z[0] - c[t]) / at for z in ob_s]
                ob_s_d[t] = d[int(np.argmin(np.abs(d)))]
                in_ob_s[t] = float(any(h[t] >= z[0] and c[t] <= z[1] for z in ob_s))
        trend[t], sh_lvl[t], sl_lvl[t] = tr, sh, sl
        since_bos[t] = min(CAP, t - lb_i)
        since_choch[t] = min(CAP, t - lc_i)
        since_sweep[t] = min(CAP, t - ls_i)
        bos_dir[t], bos_lvl[t], choch_dir[t] = (lb_d if t - lb_i < CAP else 0), lb_l, (lc_d if t - lc_i < CAP else 0)
        sweep_dir[t], sweep_ext[t] = (ls_d if t - ls_i < CAP else 0), ls_e

    return pd.DataFrame({
        "trend": trend, "sh_level": sh_lvl, "sl_level": sl_lvl,
        "bos_up": bos_up, "bos_dn": bos_dn, "choch_up": choch_up, "choch_dn": choch_dn,
        "since_bos": since_bos, "bos_dir": bos_dir, "bos_level": bos_lvl,
        "since_choch": since_choch, "choch_dir": choch_dir,
        "sweep_hi": sweep_hi_ev, "sweep_lo": sweep_lo_ev, "since_sweep": since_sweep, "sweep_dir": sweep_dir,
        "sweep_ext": sweep_ext,
        "fvg_bull_d": fvg_b_d, "fvg_bear_d": fvg_s_d, "in_fvg_bull": in_fvg_b, "in_fvg_bear": in_fvg_s,
        "ob_bull_d": ob_b_d, "ob_bear_d": ob_s_d, "in_ob_bull": in_ob_b, "in_ob_bear": in_ob_s,
    }, index=getattr(open_, "index", None))
