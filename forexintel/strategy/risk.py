"""Hard safety gates. ONLY data-quality, cost, news, exposure and risk-limit conditions may veto a trade.
A single non-confirming indicator or SMC feature is never a veto."""
from __future__ import annotations

from ..config import Settings, get_settings

STATUS_BLOCKERS = {"NO_DATA": "INSUFFICIENT_DATA", "INSUFFICIENT_DATA": "INSUFFICIENT_DATA", "STALE": "FEED_STALE",
                   "DEGRADED": "FEED_DEGRADED", "MARKET_CLOSED": "MARKET_CLOSED"}
EDGE_BLOCKERS = {"NO_VALIDATED_EDGE", "EXPECTED_VALUE_NOT_POSITIVE"}


def market_gates(quality: dict, news: dict, breaker: dict, providers: list[str], s: Settings | None = None) -> tuple[list[str], list[str]]:
    s = s or get_settings()
    blockers, warnings = [], []
    if quality["status"] in STATUS_BLOCKERS:
        blockers.append(STATUS_BLOCKERS[quality["status"]])
    if "demo" in providers:
        blockers.append("DEMO_DATA")
    if news["state"] == "BLACKOUT":
        blockers.append("NEWS_BLACKOUT")
    elif news["state"] == "UNAVAILABLE":
        (blockers if s.news_unavailable_policy == "block" else warnings).append("NEWS_CALENDAR_UNAVAILABLE")
    if breaker.get("tripped"):
        blockers.append("DAILY_CIRCUIT_BREAKER")
    return blockers, warnings


def candidate_gates(plan: dict, has_bid_ask: bool, s: Settings | None = None) -> tuple[list[str], list[str]]:
    s = s or get_settings()
    blockers, warnings = [], []
    if not plan["ok"]:
        blockers.append(plan["reject_reason"] or "SL_INVALID")
    if plan["atr"] > 0 and plan["spread"] / plan["atr"] > s.max_spread_atr:
        blockers.append("SPREAD_TOO_WIDE")
    if not has_bid_ask:
        warnings.append("SPREAD_ASSUMED_NOT_LIVE")
    return blockers, warnings
