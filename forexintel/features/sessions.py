"""DST-safe trading-session labels (Asia/London/New York) using IANA zones, plus Asia range levels."""
from __future__ import annotations

import numpy as np
import pandas as pd

# (zone, start_hour, end_hour) in LOCAL time of that zone
SESSIONS = {"asia": ("Asia/Tokyo", 9.0, 18.0), "london": ("Europe/London", 8.0, 17.0), "ny": ("America/New_York", 8.0, 17.0)}


def session_flags(idx: pd.DatetimeIndex) -> pd.DataFrame:
    out = {}
    for name, (zone, a, b) in SESSIONS.items():
        loc = idx.tz_convert(zone)
        hr = np.asarray(loc.hour) + np.asarray(loc.minute) / 60.0
        wd = np.asarray(loc.dayofweek)
        out[f"in_{name}"] = ((hr >= a) & (hr < b) & (wd < 5)).astype(float)
    df = pd.DataFrame(out, index=idx)
    df["overlap_ln"] = df["in_london"] * df["in_ny"]
    utc_min = np.asarray(idx.hour) * 60 + np.asarray(idx.minute)
    df["tod_sin"] = np.sin(2 * np.pi * utc_min / 1440.0)
    df["tod_cos"] = np.cos(2 * np.pi * utc_min / 1440.0)
    return df


def session_label(ts) -> str:
    f = session_flags(pd.DatetimeIndex([pd.Timestamp(ts).tz_convert("UTC")])).iloc[0]
    if f["overlap_ln"]:
        return "LONDON_NY_OVERLAP"
    for k, lab in (("in_london", "LONDON"), ("in_ny", "NEW_YORK"), ("in_asia", "ASIA")):
        if f[k]:
            return lab
    return "OFF_HOURS"


def asia_range(idx: pd.DatetimeIndex, high: pd.Series, low: pd.Series) -> pd.DataFrame:
    """Running high/low of the current (or most recent) Asia session; causal (cummax inside the session)."""
    flags = session_flags(idx)["in_asia"].astype(bool)
    sid = (flags & ~flags.shift(1, fill_value=False)).cumsum()
    h = high.where(flags)
    l = low.where(flags)
    ah = h.groupby(sid).cummax().ffill()
    al = l.groupby(sid).cummin().ffill()
    return pd.DataFrame({"asia_hi": ah, "asia_lo": al}, index=idx)
