"""Market-hours calendar and gap detection (UTC canonical, DST-safe via America/New_York)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TF_SECONDS

NY = "America/New_York"


def open_mask(symbol: str, idx: pd.DatetimeIndex) -> np.ndarray:
    """True where a bar opening at idx should exist.

    FX/gold close Friday 17:00 New York -> Sunday 17:00 New York (gold re-opens 18:00). XAUUSD additionally has a daily
    17:00-18:00 New York break (Mon-Thu). Holidays are not modelled: they surface as reported gaps.
    """
    ny = idx.tz_convert(NY)
    dow = np.asarray(ny.dayofweek)
    mins = np.asarray(ny.hour) * 60 + np.asarray(ny.minute)
    closed = ((dow == 4) & (mins >= 17 * 60)) | (dow == 5) | ((dow == 6) & (mins < 17 * 60))
    if symbol == "XAUUSD":
        closed |= (dow <= 3) & (mins >= 17 * 60) & (mins < 18 * 60)
        closed |= (dow == 6) & (mins < 18 * 60)             # gold re-opens Sunday 18:00 New York
    return ~closed


def is_market_open(symbol: str, ts) -> bool:
    return bool(open_mask(symbol, pd.DatetimeIndex([pd.Timestamp(ts).tz_convert("UTC")]))[0])


def expected_index(symbol: str, start, end, tf: str = "M5") -> pd.DatetimeIndex:
    step = f"{TF_SECONDS[tf]}s"
    s = pd.Timestamp(start).tz_convert("UTC").ceil(step)
    e = pd.Timestamp(end).tz_convert("UTC").floor(step)
    if e < s:
        return pd.DatetimeIndex([], tz="UTC")
    idx = pd.date_range(s, e, freq=step, tz="UTC")
    return idx[open_mask(symbol, idx)]


def find_gaps(symbol: str, present: pd.DatetimeIndex, start, end, tf: str = "M5") -> list[dict]:
    """Contiguous runs of EXPECTED bars missing from `present` between start and end (inclusive)."""
    exp = expected_index(symbol, start, end, tf)
    if len(exp) == 0:
        return []
    missing = exp.difference(present.unique())
    if len(missing) == 0:
        return []
    step = pd.Timedelta(seconds=TF_SECONDS[tf])
    pos = exp.get_indexer(missing)               # positions in the expected timeline
    gaps, run_start, prev = [], 0, 0
    for k in range(len(missing)):
        if k == 0:
            run_start = prev = k
            continue
        if pos[k] == pos[k - 1] + 1:
            prev = k
        else:
            gaps.append((missing[run_start], missing[prev]))
            run_start = prev = k
    gaps.append((missing[run_start], missing[prev]))
    return [{"start": a.isoformat(), "end": (b + step).isoformat(), "bars": int(pos[missing.get_loc(b)] - pos[missing.get_loc(a)] + 1)}
            for a, b in gaps]
