"""Chart overlays derived from the same deterministic definitions used by the strategies (display only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ts(idx, k) -> int:
    return int(idx[k].timestamp())


def overlays(F: pd.DataFrame, bars: int = 400) -> dict:
    """F: feature frame (with structure columns). Returns markers, swing lines and FVG/OB zones for the last `bars`."""
    n = len(F)
    a0 = max(0, n - bars)
    idx = F.index
    markers = []
    for col, text, pos, shape, color in (("bos_up", "BOS", "belowBar", "arrowUp", "#26a69a"), ("bos_dn", "BOS", "aboveBar", "arrowDown", "#ef5350"),
                                         ("choch_up", "CHoCH", "belowBar", "arrowUp", "#00bcd4"), ("choch_dn", "CHoCH", "aboveBar", "arrowDown", "#ff9800"),
                                         ("sweep_lo", "Sweep", "belowBar", "circle", "#ab47bc"), ("sweep_hi", "Sweep", "aboveBar", "circle", "#ab47bc")):
        for k in np.flatnonzero(F[col].to_numpy()[a0:]) + a0:
            markers.append({"time": _ts(idx, k), "position": pos, "shape": shape, "color": color, "text": text, "kind": col})
    h, l, c, o, a = (F[x].to_numpy() for x in ("high", "low", "close", "open", "atr"))
    zones = []
    for t in range(max(2, a0), n):
        at = a[t]
        if not np.isfinite(at) or at <= 0:
            continue
        if l[t] > h[t - 2] and (l[t] - h[t - 2]) >= 0.2 * at:
            active = bool(np.all(c[t + 1:] >= h[t - 2])) if t + 1 < n else True
            zones.append({"type": "FVG_BULL", "t0": _ts(idx, t - 2), "lo": float(h[t - 2]), "hi": float(l[t]), "active": active})
        if h[t] < l[t - 2] and (l[t - 2] - h[t]) >= 0.2 * at:
            active = bool(np.all(c[t + 1:] <= l[t - 2])) if t + 1 < n else True
            zones.append({"type": "FVG_BEAR", "t0": _ts(idx, t - 2), "lo": float(h[t]), "hi": float(l[t - 2]), "active": active})
        brk_up = bool(F["bos_up"].iat[t] or F["choch_up"].iat[t])
        brk_dn = bool(F["bos_dn"].iat[t] or F["choch_dn"].iat[t])
        if brk_up or brk_dn:
            for j in range(t - 1, max(-1, t - 11), -1):
                if brk_up and c[j] < o[j] and (c[t] - l[j]) >= 1.2 * at:
                    zones.append({"type": "OB_BULL", "t0": _ts(idx, j), "lo": float(l[j]), "hi": float(h[j]),
                                  "active": bool(np.all(c[t + 1:] >= l[j])) if t + 1 < n else True})
                    break
                if brk_dn and c[j] > o[j] and (h[j] - c[t]) >= 1.2 * at:
                    zones.append({"type": "OB_BEAR", "t0": _ts(idx, j), "lo": float(l[j]), "hi": float(h[j]),
                                  "active": bool(np.all(c[t + 1:] <= h[j])) if t + 1 < n else True})
                    break
    last = F.iloc[-1]
    lines = []
    for key, label in (("sh_level", "swing high"), ("sl_level", "swing low"), ("asia_hi", "Asia high"), ("asia_lo", "Asia low")):
        v = last.get(key)
        if pd.notna(v):
            lines.append({"price": float(v), "label": label})
    zones = [z for z in zones if z["active"]][-12:]
    markers.sort(key=lambda m: m["time"])
    return {"markers": markers, "zones": zones, "lines": lines}
