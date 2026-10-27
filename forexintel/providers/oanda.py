"""OANDA v20 REST adapter (free practice/demo account token). Real bid/ask candles + pricing snapshot.

Docs: https://developer.oanda.com/rest-live-v20/ . Eligibility depends on the OANDA entity holding the account
(all divisions except OANDA Global Markets and OANDA TMS BROKERS S.A. per OANDA's docs).
NOT verified end-to-end by this project: no credentials were available. Parsing is unit-tested against the
documented response shape; run `python main.py doctor` with your token to verify.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from ..config import get_settings
from .base import MarketDataProvider, ProviderBlocked, ProviderError, empty_frame, from_bid_ask, http_get

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}


def parse_candles(payload: dict) -> pd.DataFrame:
    rows = []
    for c in payload.get("candles", []):
        if "bid" not in c or "ask" not in c:
            continue
        rows.append((pd.Timestamp(c["time"]).tz_convert("UTC").floor("s"), bool(c.get("complete", False)),
                     {k: float(v) for k, v in c["bid"].items()}, {k: float(v) for k, v in c["ask"].items()},
                     float(c.get("volume", 0))))
    if not rows:
        return empty_frame()
    idx = pd.DatetimeIndex([r[0] for r in rows])
    full = lambda side: pd.DataFrame({  # noqa: E731
        "open": [r[side]["o"] for r in rows], "high": [r[side]["h"] for r in rows],
        "low": [r[side]["l"] for r in rows], "close": [r[side]["c"] for r in rows],
        "volume": [r[4] for r in rows]}, index=idx)
    out = from_bid_ask(full(2), full(3))
    done = pd.Series([r[1] for r in rows], index=idx)
    out["is_complete"] = done.reindex(out.index).fillna(False).to_numpy(dtype=bool)
    return out


class OandaProvider(MarketDataProvider):
    name = "oanda"
    verified = False
    has_bid_ask = True
    streaming = False

    def _auth(self) -> dict:
        s = get_settings()
        if not s.oanda_token:
            raise ProviderBlocked("OANDA_TOKEN not set (free practice token: OANDA account portal > Manage API Access)")
        return {"Authorization": f"Bearer {s.oanda_token}"}

    def _host(self) -> str:
        return HOSTS.get(get_settings().oanda_env, HOSTS["practice"])

    def fetch_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe != "M5":
            raise ProviderError("oanda adapter fetches M5 as the base timeframe")
        h, inst = self._auth(), self.provider_symbol(symbol)
        frames, cur = [], start
        step = timedelta(minutes=5 * 4500)
        while cur < end:
            to = min(cur + step, end)
            r = http_get(f"{self._host()}/v3/instruments/{inst}/candles", headers=h, params={
                "granularity": "M5", "price": "BA", "from": cur.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "to": to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), "includeFirst": "true"})
            frames.append(parse_candles(r.json()))
            cur = to
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames) if frames else empty_frame()

    def latest_quote(self, symbol: str) -> dict | None:
        s = get_settings()
        if not s.oanda_account:
            raise ProviderBlocked("OANDA_ACCOUNT_ID not set")
        r = http_get(f"{self._host()}/v3/accounts/{s.oanda_account}/pricing", headers=self._auth(),
                     params={"instruments": self.provider_symbol(symbol)})
        p = r.json()["prices"][0]
        bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "spread": ask - bid, "source_ts": p["time"], "tradeable": p.get("tradeable")}

    def health(self) -> dict:
        try:
            self._auth()
        except ProviderBlocked as e:
            return {"provider": self.name, "ok": False, "blocked": True, "detail": str(e)}
        try:
            r = http_get(f"{self._host()}/v3/accounts", headers=self._auth(), tries=2)
            return {"provider": self.name, "ok": True, "accounts": len(r.json().get("accounts", []))}
        except ProviderError as e:
            return {"provider": self.name, "ok": False, "detail": str(e)}
