"""Analysis engine: data gate -> regime -> directional forecast -> independent strategies -> validated-edge ranking ->
hard safety gates -> published result. Directional forecast and trade authorization are always separate outputs."""
from __future__ import annotations

import time
from collections import OrderedDict

import numpy as np
import pandas as pd
from sqlalchemy import select

from .. import db
from ..config import INSTRUMENTS, TF_SECONDS, Settings, get_settings, normalize_symbol
from ..data import quality, store
from ..features import news as newsmod
from ..features.sessions import session_label
from ..paper import journal
from ..research import registry
from ..research.dataset import direction_features, event_row, model_matrix, prepare
from ..research.metrics import clean
from . import strategies as S
from .plan import expected_move_pct, make_plan
from .risk import EDGE_BLOCKERS, candidate_gates, market_gates

LIVE_WINDOW = 6000        # fixed look-back so a replay of the same snapshot yields the identical decision
FORECAST_HORIZONS = (3, 6, 12)
PRIMARY_HORIZON = 6
_cache: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()


def _features(symbol: str, m5: pd.DataFrame) -> pd.DataFrame:
    key = (symbol, m5.index[-1], len(m5))
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    F = prepare(m5.iloc[-LIVE_WINDOW:])
    _cache[key] = F
    while len(_cache) > 6:
        _cache.popitem(last=False)
    return F


def _display(action: str, strength: str) -> str:
    if action == "LONG":
        return "STRONG BUY" if strength == "STRONG" else "BUY"
    if action == "SHORT":
        return "STRONG SELL" if strength == "STRONG" else "SELL"
    return "WAIT" if action == "NO_TRADE" else "CANNOT ASSESS"


def rule_bias(row: pd.Series) -> tuple[str, float, list[str]]:
    """Transparent rules-only direction score (NOT a probability)."""
    h1, m15, m5 = (float(row.get(k) or 0) for k in ("h1_trend", "m15_trend", "trend"))
    sl = float(row.get("ema50_slope") or 0)
    e200 = row.get("h1_ema200_d")
    score = 2.0 * h1 + 1.5 * m15 + 1.0 * m5 + 0.5 * np.sign(sl) + (0.5 * np.sign(e200) if pd.notna(e200) else 0.0)
    ev = [f"H1 structure {'up' if h1 > 0 else 'down' if h1 < 0 else 'unclear'}", f"M15 structure {'up' if m15 > 0 else 'down' if m15 < 0 else 'unclear'}",
          f"M5 structure {'up' if m5 > 0 else 'down' if m5 < 0 else 'unclear'}"]
    return ("UP" if score >= 2.0 else "DOWN" if score <= -2.0 else "NEUTRAL"), float(score), ev


def directional_forecast(symbol: str, F: pd.DataFrame, row: pd.Series) -> dict:
    bias, score, evidence = rule_bias(row)
    out = {"bias": bias, "horizon_m5_bars": PRIMARY_HORIZON, "probability": None, "status": "NOT_CALIBRATED",
           "method": "rules (H1/M15/M5 structure score)", "rule_score": score, "evidence": evidence, "model_version": None,
           "typical_move_pct": float(row["atr"] * np.sqrt(PRIMARY_HORIZON) / row["close"] * 100), "by_horizon": {}}
    X = None
    for h in FORECAST_HORIZONS:
        m = registry.active_model(symbol, f"direction_h{h}")
        if not m:
            continue
        hl = registry.health(m)
        if hl["state"] != "OK":
            continue
        X = direction_features(F.iloc[[-1]]) if X is None else X
        b = registry.load_bundle(m)
        p = float(b.proba(X)[0])
        mb = "UP" if p >= 0.55 else "DOWN" if p <= 0.45 else "NEUTRAL"
        out["by_horizon"][h] = {"p_up": p, "bias": mb, "model_version": m["version"]}
        if h == PRIMARY_HORIZON:
            out.update(bias=mb, probability=p, status="CALIBRATED", method=f"{b.algo} (calibrated P(up))", model_version=m["version"])
    return out


def _strategy_stats(symbol: str) -> dict:
    t = db.strategy_stats
    with db.get_engine().connect() as c:
        return {r.strategy: dict(r._mapping) for r in c.execute(select(t).where(t.c.symbol == symbol))}


def _hold_minutes(stat: dict | None, horizon: int) -> list[int]:
    d = (stat or {}).get("detail") or {}
    hb = d.get("hold_bars_win")
    if hb:
        return [int(hb[0] * 5), int(hb[2] * 5)]
    return [5, horizon * 5]


def _evaluate(symbol: str, F: pd.DataFrame, i: int, name: str, side: int, plan: dict, bundle, stat: dict | None, s: Settings) -> dict:
    """Edge evidence for one candidate: calibrated ML if an approved healthy model exists, else rules-only stats."""
    cand = {"strategy": name, "side": "LONG" if side == 1 else "SHORT", "plan": plan, "evidence": "NONE", "p": None, "ev_r": None,
            "strength": "NONE", "edge_ok": False, "edge_note": ""}
    if bundle is not None and bundle.threshold is not None:
        ev = pd.DataFrame([event_row(F, i, name, side, plan)])
        p = float(bundle.proba(model_matrix(ev))[0])
        ev_hat = float(bundle.ev_hat(p))
        cand.update(evidence="ML_CALIBRATED", p=p, ev_r=ev_hat)
        if p >= bundle.threshold and ev_hat >= s.min_ev_r:
            cand["edge_ok"] = True
            cand["strength"] = "STRONG" if (ev_hat >= s.strong_ev_r and p >= bundle.threshold + 0.05) else "MODERATE"
        else:
            cand["edge_note"] = f"calibrated P(TP1)={p:.2f} below model threshold {bundle.threshold:.2f} or EV {ev_hat:+.2f}R < {s.min_ev_r}R"
        return cand
    if stat and stat.get("n"):
        d = stat.get("detail") or {}
        cand.update(evidence="RULES_BACKTEST", ev_r=stat["ev_r"], hist_win_rate=stat["win_rate"], hist_n=stat["n"])
        stable = d.get("halves_positive", False)
        if stat["n"] >= s.min_samples and stat["ev_r"] is not None and stat["ev_r"] >= s.min_ev_r and stable:
            cand["edge_ok"] = True
            cand["strength"] = "STRONG" if (stat.get("ev_lo") or -1) > 0 and stat["ev_r"] >= s.strong_ev_r else "MODERATE"
        else:
            cand["edge_note"] = (f"rules backtest: n={stat['n']}, net EV {stat['ev_r']:+.2f}R"
                                 f"{'' if stable else ', not positive in both halves'} - no validated edge")
    else:
        cand["edge_note"] = "no backtest evidence yet: run `python main.py backtest`"
    return cand


def analyze(symbol: str, now=None, persist: bool = True, track_paper: bool = True, s: Settings | None = None) -> dict:
    s = s or get_settings()
    symbol = normalize_symbol(symbol)
    ins = INSTRUMENTS[symbol]
    db.init_db()
    now_ts = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    m5, meta = store.load_series(symbol)
    if now is not None:
        m5 = m5[m5.index + pd.Timedelta(minutes=5) <= now_ts]
    q = quality.assess(symbol, m5, now_ts)
    providers = [p for p in (meta["history_provider"], meta["live_provider"]) if p]
    base = {"symbol": symbol, "display": ins.display, "generated_utc": now_ts.isoformat(), "provider": "+".join(providers),
            "lineage": meta, "data_quality": q, "feed_status": q["status"], "blockers": [], "warnings": [], "reasons": []}
    if q["status"] in ("NO_DATA", "INSUFFICIENT_DATA"):
        base.update(asof_utc=None, regime=None, trade_action="CANNOT_ASSESS", display_action="CANNOT ASSESS", strength="NONE",
                    directional_forecast={"bias": None, "status": "INSUFFICIENT_DATA", "probability": None},
                    blockers=["INSUFFICIENT_DATA"], data_mode="NO_DATA", candidates=[], model_version=None)
        return base
    F = _features(symbol, m5)
    i = len(F) - 1
    row = F.iloc[i]
    decision_ts = F.index[i] + pd.Timedelta(seconds=TF_SECONDS["M5"])
    news = newsmod.blackout(symbol, now_ts)
    breaker = journal.circuit_breaker(now_ts)
    forecast = directional_forecast(symbol, F, row)
    mkt_blockers, warnings = market_gates(q, news, breaker, providers, s)

    # model + stats
    mrow = registry.active_model(symbol, "trade")
    bundle, mhealth = None, registry.health(mrow)
    if mrow and mhealth["state"] == "OK":
        bundle = registry.load_bundle(mrow)
    elif mrow:
        warnings.append("MODEL_" + "_".join(mhealth["flags"]))
    stats = _strategy_stats(symbol)

    tail = F.iloc[-800:]
    gens = S.generate(tail)
    last = len(tail) - 1
    cands = []
    for name, g in gens.items():
        for side, col, anc in ((1, "long", "long_anchor"), (-1, "short", "short_anchor")):
            if not bool(g[col].iloc[last]):
                continue
            plan = make_plan(row, side, g[anc].iloc[last], s, symbol)
            c = _evaluate(symbol, F, i, name, side, plan, bundle, stats.get(name), s)
            c["reasons"] = S.explain(name, row, side)
            c_blockers, c_warn = candidate_gates(plan, bool(row["has_bid_ask"]), s)
            c["gate_blockers"] = c_blockers
            c["warnings"] = c_warn
            cands.append(c)

    def rank(c):
        return (c["edge_ok"] and not c["gate_blockers"], c["ev_r"] if c["ev_r"] is not None else -9.0)
    cands.sort(key=rank, reverse=True)
    top = cands[0] if cands else None

    res = dict(base)
    res.update(asof_utc=decision_ts.isoformat(), bar_open_utc=F.index[i].isoformat(), regime=row["regime"], vol_state=row["vol_state"],
               session=session_label(decision_ts - pd.Timedelta(seconds=1)), directional_forecast=forecast,
               model_version=mrow["version"] if mrow and bundle else None, news=news, warnings=warnings,
               price={"bid": round(float(row["bc"]), ins.quote_precision), "ask": round(float(row["ac"]), ins.quote_precision),
                      "mid": round(float(row["close"]), ins.quote_precision), "spread": round(float(row["spread"]), ins.quote_precision + 1),
                      "spread_is_real": bool(row["has_bid_ask"]), "atr": round(float(row["atr"]), ins.quote_precision + 1)},
               data_mode=("DEMO" if "demo" in providers else "LIVE" if q["status"] == "HEALTHY" else "MARKET_CLOSED" if q["status"] == "MARKET_CLOSED" else "STALE"),
               circuit_breaker=breaker)
    blockers = list(mkt_blockers)
    if not bool(row["has_bid_ask"]):
        warnings.append("SPREAD_ASSUMED_NOT_LIVE")
    if abs(meta.get("seam_jump") or 0.0) > 0.5 * float(row["atr"]):
        warnings.append("SOURCE_SEAM_PRICE_JUMP")       # history and live sources disagree by more than half an ATR
    res.update(trade_action="NO_TRADE", strength="NONE", candidate_strategy=None, entry_zone=None, sl=None, tp1=None, tp2=None,
               tp3=None, probability={"value": None, "status": "NOT_APPLICABLE"}, expected_value_r=None, expected_move_pct=None,
               holding_minutes=None, valid_until_utc=None, rr=None)
    if top is None:
        blockers.append("NO_SETUP")
        res["reasons"] = ["no strategy produced a candidate on this closed M5 bar (regime " + str(row["regime"]) + ")"]
    else:
        plan = top["plan"]
        top_blockers = list(top["gate_blockers"])
        if not top["edge_ok"]:
            top_blockers.append("EXPECTED_VALUE_NOT_POSITIVE" if top["evidence"] == "ML_CALIBRATED" else "NO_VALIDATED_EDGE")
        sid = f"{symbol}-{int(decision_ts.timestamp())}-{base['provider']}"
        exp = journal.exposure_blockers(symbol, plan["side"], sid) if track_paper else []
        all_b = blockers + top_blockers + exp
        res["candidate_strategy"] = top["strategy"]
        res["reasons"] = top["reasons"] + ([top["edge_note"]] if top["edge_note"] else [])
        res["expected_value_r"] = top["ev_r"]
        res["plan_preview"] = {k: (None if v is None else float(v)) for k, v in plan.items() if k not in ("ok", "reject_reason", "side")}
        if not all_b:
            res.update(trade_action="LONG" if plan["side"] == 1 else "SHORT", strength=top["strength"])
            res["entry_zone"] = [round(plan["entry_lo"], ins.quote_precision), round(plan["entry_hi"], ins.quote_precision)]
            for k in ("sl", "tp1", "tp2", "tp3"):
                res[k] = None if plan[k] is None else round(float(plan[k]), ins.quote_precision)
            res["rr"] = {"tp1": plan["rr1"], "tp2": plan["rr2"], "tp3": plan["rr3"]}
            res["expected_move_pct"] = {k: (None if plan[k] is None else round(expected_move_pct(plan["entry_ref"], plan[k]), 4))
                                        for k in ("tp1", "tp2", "tp3")}
            res["holding_minutes"] = _hold_minutes(stats.get(top["strategy"]), s.horizon_bars)
            res["valid_until_utc"] = (decision_ts + pd.Timedelta(seconds=TF_SECONDS["M5"] * s.signal_expiry_bars)).isoformat()
            if top["evidence"] == "ML_CALIBRATED":
                res["probability"] = {"value": top["p"], "status": "CALIBRATED",
                                      "definition": f"P(TP1 hit before SL within {s.horizon_bars} M5 bars, after costs)"}
            else:
                res["probability"] = {"value": None, "status": "NOT_CALIBRATED",
                                      "historical_win_rate": top.get("hist_win_rate"), "samples": top.get("hist_n")}
        blockers = all_b
    res["blockers"] = blockers
    res["display_action"] = _display(res["trade_action"], res["strength"])
    res["candidates"] = [{k: v for k, v in c.items() if k != "plan"} | {"plan": {kk: (None if vv is None else float(vv)) for kk, vv in c["plan"].items()
                          if kk not in ("ok", "reject_reason")}} for c in cands]
    res["signal_id"] = f"{symbol}-{int(decision_ts.timestamp())}-{res['provider']}"
    res = clean(res)
    if persist:
        _persist(res, top, decision_ts, s, track_paper, mkt_blockers)
    return res


def _persist(res: dict, top: dict | None, decision_ts: pd.Timestamp, s: Settings, track_paper: bool, mkt_blockers: list) -> None:
    sid = res["signal_id"]
    row = dict(id=sid, created_ts=int(time.time()), asof_ts=int(decision_ts.timestamp()), symbol=res["symbol"], provider=res["provider"],
               feed_status=res["feed_status"], regime=res["regime"], bias=res["directional_forecast"].get("bias"),
               bias_prob=res["directional_forecast"].get("probability"), action=res["trade_action"], strength=res["strength"],
               strategy=res["candidate_strategy"], entry=(res["entry_zone"][0] if res["entry_zone"] else None), sl=res["sl"],
               tp1=res["tp1"], tp2=res["tp2"], tp3=res["tp3"], prob=(res["probability"] or {}).get("value"), ev_r=res["expected_value_r"],
               expiry_ts=int(pd.Timestamp(res["valid_until_utc"]).timestamp()) if res["valid_until_utc"] else None,
               blockers=res["blockers"], payload=res, model_version=res["model_version"],
               state="ARMED" if res["trade_action"] in ("LONG", "SHORT") else "NO_TRADE")
    with db.begin() as c:
        db.upsert(c, db.signals, [row], ["symbol", "asof_ts", "provider"])
        c.execute(db.strategy_candidates.delete().where(db.strategy_candidates.c.signal_id == sid))
        for k, cand in enumerate(res.get("candidates", [])):
            c.execute(db.strategy_candidates.insert().values(
                signal_id=sid, symbol=res["symbol"], asof_ts=int(decision_ts.timestamp()), strategy=cand["strategy"], side=cand["side"],
                regime=res["regime"], plan=cand["plan"], status="SELECTED" if k == 0 else "NOT_SELECTED",
                reasons=cand.get("reasons"), rank=float(cand["ev_r"]) if cand.get("ev_r") is not None else None))
    if track_paper and top is not None and res["feed_status"] == "HEALTHY" and "demo" not in res["provider"]:
        only_edge = set(res["blockers"]) <= EDGE_BLOCKERS | {"NO_VALIDATED_EDGE", "EXPECTED_VALUE_NOT_POSITIVE"}
        if res["trade_action"] in ("LONG", "SHORT") or (res["blockers"] and only_edge and not mkt_blockers):
            plan = {k: (v if k not in ("ok", "reject_reason") else v) for k, v in top["plan"].items()}
            journal.open_position(sid, res["symbol"], int(decision_ts.timestamp()) - TF_SECONDS["M5"], plan, top["strategy"],
                                  authorized=res["trade_action"] in ("LONG", "SHORT"))
