"""Dukascopy public historical datafeed: free, real BID and ASK 1-minute candles (aggregated to M5).

Files: https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM-1:02d}/{DD}/{BID|ASK}_candles_min_1.bi5
LZMA-compressed, 24-byte big-endian records: (sec_of_day, open, close, low, high, volume).
Daily files appear with a lag (roughly a day), so this provider is for research/training and
"as of last available data" analysis; pair it with a live provider for fresh signals.
Licensing: public delayed data intended for personal/research use; do not redistribute.
"""
from __future__ import annotations

import lzma
import random
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..config import get_settings
from .base import MarketDataProvider, ProviderError, empty_frame, from_bid_ask

BASE = "https://datafeed.dukascopy.com/datafeed"
SCALE = {"XAUUSD": 1000.0}          # gold quoted with 3 decimals; FX majors 5
HEADERS = {"User-Agent": "Mozilla/5.0 (forexintel research)"}
REC = struct.Struct(">iiiiif")


def parse_bi5(raw: bytes, day: datetime, scale: float) -> pd.DataFrame:
    """Decode one decompressed daily file into a 1-minute frame indexed by UTC open time."""
    n = len(raw) // REC.size
    if n == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    arr = np.frombuffer(raw[: n * REC.size],
                        dtype=[("s", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])
    day0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([day0 + timedelta(seconds=int(s)) for s in arr["s"]])
    return pd.DataFrame({"open": arr["o"] / scale, "high": arr["h"] / scale, "low": arr["l"] / scale,
                         "close": arr["c"] / scale, "volume": arr["v"].astype("float64")}, index=idx)


def to_m5(m1: pd.DataFrame) -> pd.DataFrame:
    if m1.empty:
        return m1
    # Dukascopy pads closed periods (weekends, breaks) with flat zero-volume placeholder candles: drop them.
    m1 = m1[~((m1["volume"] <= 0) & (m1["high"] == m1["low"]))]
    if m1.empty:
        return m1
    r = m1.resample("5min", label="left", closed="left")
    out = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(),
                        "close": r["close"].last(), "volume": r["volume"].sum()})
    return out.dropna(subset=["open", "close"])


class DukascopyProvider(MarketDataProvider):
    name = "dukascopy"
    verified = True      # download + decoding verified against live files for EURUSD / XAUUSD (Oct 2026)
    has_bid_ask = True
    streaming = False

    def __init__(self, workers: int = 4, cache: bool = True):
        self.workers = workers
        self.cache_dir = get_settings().data_dir / "cache" / "dukascopy" if cache else None
        self.session = requests.Session()
        self.last_report: dict = {}

    def _url(self, sym: str, day: datetime, side: str) -> str:
        return f"{BASE}/{sym}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"

    def _cache_path(self, sym: str, day: datetime, side: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / sym / f"{day:%Y-%m-%d}_{side}.bi5"

    def _fetch_day(self, sym: str, day: datetime, side: str) -> bytes | None:
        """Returns raw (decompressed) bytes; b'' = no data that day; None = failed after retries."""
        cp = self._cache_path(sym, day, side)
        settled = (datetime.now(timezone.utc).date() - day.date()).days >= 3
        if cp is not None and cp.exists() and settled:
            return cp.read_bytes()
        url = self._url(sym, day, side)
        for n in range(6):
            wait = min(40.0, (2.0 ** n) + random.random() * 2)
            try:
                r = self.session.get(url, headers=HEADERS, timeout=45)
            except requests.RequestException:
                time.sleep(wait)
                continue
            if r.status_code == 200:
                try:
                    raw = lzma.decompress(r.content) if r.content else b""
                except lzma.LZMAError:
                    time.sleep(wait)
                    continue
                if cp is not None and settled:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_bytes(raw)
                return raw
            if r.status_code == 404:
                if cp is not None and settled:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_bytes(b"")
                return b""
            time.sleep(wait)        # 503 = throttled; back off and retry
        return None

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe != "M5":
            raise ProviderError("dukascopy adapter provides M5 (aggregated from 1-minute bid/ask)")
        sym = self.provider_symbol(symbol)
        scale = SCALE.get(sym, 100000.0)
        d0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        d1 = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        days = [d for d in days if d.weekday() != 5]          # Saturday: market closed
        jobs = [(d, s) for d in days for s in ("BID", "ASK")]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            res = list(ex.map(lambda j: self._fetch_day(sym, j[0], j[1]), jobs))
        raw: dict = {}
        failed: set = set()
        for (d, s), b in zip(jobs, res):
            if b is None:
                failed.add(d.date().isoformat())
            else:
                raw[(d, s)] = b
        bids, asks = [], []
        for d in days:
            if d.date().isoformat() in failed:
                continue
            bb, aa = raw.get((d, "BID"), b""), raw.get((d, "ASK"), b"")
            if bb:
                bids.append(to_m5(parse_bi5(bb, d, scale)))
            if aa:
                asks.append(to_m5(parse_bi5(aa, d, scale)))
        self.last_report = {"days_requested": len(days), "failed_days": sorted(failed)}
        if not bids or not asks:
            return empty_frame()
        frame = from_bid_ask(pd.concat(bids), pd.concat(asks))
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        return frame[(frame.index >= lo) & (frame.index <= hi)]

    def health(self) -> dict:
        try:
            day = datetime.now(timezone.utc) - timedelta(days=4)
            r = requests.head(self._url("EURUSD", day, "BID"), headers=HEADERS, timeout=20)
            return {"provider": self.name, "ok": r.status_code in (200, 404), "http": r.status_code,
                    "note": "free delayed history (~1 day lag), real bid/ask"}
        except requests.RequestException as e:
            return {"provider": self.name, "ok": False, "detail": e.__class__.__name__}
