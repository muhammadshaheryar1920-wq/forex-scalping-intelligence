"""Market regime classification from past-only features (no future data, no fitted-on-test thresholds)."""
from __future__ import annotations

import numpy as np
import pandas as pd

ADX_TREND_M15 = 22.0
ADX_TREND_H1 = 25.0
VOL_HIGH_RANK = 0.90      # ATR percentile vs the preceding 500 bars (adaptive, causal)
VOL_QUIET_RANK = 0.15


def classify(F: pd.DataFrame) -> pd.DataFrame:
    """Adds `base_regime` (TREND_UP/TREND_DOWN/RANGE), `vol_state` (HIGH/NORMAL/QUIET) and display `regime`."""
    h1t = F["h1_trend"].fillna(0)
    m15t = F["m15_trend"].fillna(0)
    trending = (F["m15_adx"].fillna(0) >= ADX_TREND_M15) | (F["h1_adx"].fillna(0) >= ADX_TREND_H1)
    direction = np.sign(2 * h1t + m15t)
    base = np.where(trending & (direction > 0), "TREND_UP",
                    np.where(trending & (direction < 0), "TREND_DOWN", "RANGE"))
    rank = F["atr_rank"]
    vol = np.where(rank > VOL_HIGH_RANK, "HIGH", np.where(rank < VOL_QUIET_RANK, "QUIET", "NORMAL"))
    out = pd.DataFrame(index=F.index)
    out["base_regime"] = base
    out["vol_state"] = vol
    out["regime"] = np.where(vol == "HIGH", "HIGH_VOL", np.where(vol == "QUIET", "QUIET", base))
    return out
