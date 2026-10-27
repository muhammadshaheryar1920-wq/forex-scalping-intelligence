"""FastAPI service (versioned /api/v1) + static dashboard. Local, loopback by default, paper-trading only."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from .. import db
from ..config import INSTRUMENTS, SYMBOLS, get_settings, normalize_symbol
from ..data import store, sync as sy
from ..features import news as newsmod
from ..features.builder import _base
from ..features.overlays import overlays
from ..paper import journal
from ..providers import PROVIDER_NAMES, get_provider
from ..research import backtest as bt, registry, train as tr
from ..research.metrics import clean
from ..strategy import engine
from . import jobs

STATIC = Path(__file__).parent / "static"
VERSION = "1.0.0"
STREAM_INTERVAL = 10.0


class Monitor:
    """Optional in-process monitor: analyses on M5 closes while the server (and this machine) is running."""

    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.state = {"running": False, "cycles": 0, "last_cycle_utc": None, "last_error": None}

    def start(self, symbols=SYMBOLS) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._loop, args=(tuple(symbols),), daemon=True, name="monitor")
        self.thread.start()
        self.state["running"] = True

    def pause(self) -> None:
        self.stop.set()
        self.state["running"] = False

    def _loop(self, symbols) -> None:
        while not self.stop.is_set():
            try:
                for sym in symbols:
                    sy.sync_composed(sym, history=False, live=True)
                    engine.analyze(sym)
                    journal.advance(sym, store.load_series(sym)[0])
                self.state.update(cycles=self.state["cycles"] + 1, last_cycle_utc=datetime.now(timezone.utc).isoformat(), last_error=None)
            except Exception as e:  # noqa: BLE001
                self.state["last_error"] = f"{type(e).__name__}: {e}"
            now = datetime.now(timezone.utc)
            wait = 300 - (now.minute % 5) * 60 - now.second + 20
            self.stop.wait(max(20, wait))
        self.state["running"] = False


monitor = Monitor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    db.seed_instruments()
    yield
    monitor.pause()


app = FastAPI(title="Forex Scalping Intelligence (local, paper-only)", version=VERSION, lifespan=lifespan)
ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000", "http://127.0.0.1:8000", "http://localhost:8000"]
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"] + [h for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)      # blocks DNS-rebinding style access


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """Reject state-changing requests that a foreign web page could trigger in the user's browser (CSRF)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
    return await call_next(request)


def auth(authorization: str | None = Header(None), x_api_key: str | None = Header(None), token: str | None = Query(None)):
    want = get_settings().api_token
    if not want:
        return
    got = x_api_key or (authorization or "").removeprefix("Bearer ").strip() or token
    if got != want:
        raise HTTPException(401, "invalid or missing API token")


def _sym(s: str) -> str:
    try:
        return normalize_symbol(s)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


api = "/api/v1"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get(f"{api}/health")
def health():
    s = get_settings()
    try:
        with db.get_engine().connect() as c:
            c.execute(select(1))
        dbok = True
    except Exception:  # noqa: BLE001
        dbok = False
    return {"status": "ok" if dbok else "degraded", "version": VERSION, "time_utc": datetime.now(timezone.utc).isoformat(), "db": dbok,
            "history_provider": s.history_provider, "live_provider": s.live_provider, "paper_only": True,
            "auth_required": bool(s.api_token), "monitor": monitor.state}


@app.get(f"{api}/providers/health", dependencies=[Depends(auth)])
async def providers_health(deep: bool = False):
    s = get_settings()
    names = [n for n in dict.fromkeys([s.history_provider, s.live_provider]) if n and n != "none"]

    def work():
        out = []
        for n in names:
            p = get_provider(n)
            h = p.health() if deep or n in ("oanda", "twelvedata", "mt5", "demo") else {"provider": n, "ok": None, "note": "pass deep=true for a network test"}
            out.append({"health": h, "capabilities": p.capabilities(), "role": "history" if n == s.history_provider else "live"})
        return out
    return await run_in_threadpool(work)


@app.get(f"{api}/settings", dependencies=[Depends(auth)])
def settings_view():
    s = get_settings()
    return {"history_provider": s.history_provider, "live_provider": s.live_provider,
            "credentials": {"twelvedata_key": bool(s.twelvedata_key), "oanda_token": bool(s.oanda_token), "oanda_account": bool(s.oanda_account),
                            "api_token": bool(s.api_token)},
            "risk": {"paper_equity": s.paper_equity, "risk_per_trade_pct": s.risk_per_trade_pct, "daily_loss_limit_pct": s.daily_loss_limit_pct,
                     "max_spread_atr": s.max_spread_atr, "news_pre_minutes": s.news_pre_minutes, "news_post_minutes": s.news_post_minutes,
                     "news_unavailable_policy": s.news_unavailable_policy, "horizon_bars": s.horizon_bars, "tp1_r": s.tp1_r,
                     "min_samples": s.min_samples, "min_ev_r": s.min_ev_r},
            "instruments": {k: {"display": v.display, "assumed_spread": v.assumed_spread, "commission_rt": v.commission_rt,
                                "providers": v.provider_symbols} for k, v in INSTRUMENTS.items()},
            "providers_available": list(PROVIDER_NAMES), "sessions_tz": ["Asia/Tokyo", "Europe/London", "America/New_York"]}


@app.get(f"{api}/markets/{{symbol}}/snapshot", dependencies=[Depends(auth)])
def snapshot(symbol: str):
    symbol = _sym(symbol)
    m5, meta = store.load_series(symbol)
    from ..data import quality
    q = quality.assess(symbol, m5)
    with db.get_engine().connect() as c:
        states = [dict(r._mapping) for r in c.execute(select(db.sync_state).where(db.sync_state.c.symbol == symbol))]
    out = {"symbol": symbol, "display": INSTRUMENTS[symbol].display, "quality": q, "lineage": meta, "sync_state": clean(states)}
    if len(m5):
        r = m5.iloc[-1]
        out["last_bar"] = {"open_utc": m5.index[-1].isoformat(), "bid": float(r["bc"]), "ask": float(r["ac"]), "mid": float(r["close"]),
                           "spread": float(r["ac"] - r["bc"]), "spread_is_real": bool(r["has_bid_ask"])}
    return clean(out)


@app.get(f"{api}/markets/{{symbol}}/candles", dependencies=[Depends(auth)])
def candles(symbol: str, timeframe: str = "M5", limit: int = Query(400, ge=20, le=3000), overlay: bool = True):
    symbol = _sym(symbol)
    tf = timeframe.upper()
    if tf not in ("M5", "M15", "H1"):
        raise HTTPException(400, "timeframe must be M5, M15 or H1")
    m5, meta = store.load_series(symbol)
    if m5.empty:
        return {"symbol": symbol, "timeframe": tf, "candles": [], "overlays": None, "lineage": meta}
    frame = store.resample(m5, tf)
    tail = frame.iloc[-limit:]
    out = {"symbol": symbol, "timeframe": tf, "lineage": meta, "candles": [
        {"time": int(t.timestamp()), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)}
        for t, r in zip(tail.index, tail.itertuples())]}
    if overlay and len(m5) > 2700:
        try:
            if tf == "M5":
                F = engine._features(symbol, m5)
            else:
                b, *_ = _base(frame, 3 if tf == "M15" else 2)
                F = frame[["open", "high", "low", "close"]].join(b)
            out["overlays"] = overlays(F, bars=min(limit, 400))
        except Exception as e:  # noqa: BLE001
            out["overlays"] = None
            out["overlay_error"] = f"{type(e).__name__}: {e}"
    return clean(out)


@app.get(f"{api}/markets/{{symbol}}/analysis", dependencies=[Depends(auth)])
async def analysis(symbol: str):
    symbol = _sym(symbol)
    return await run_in_threadpool(lambda: engine.analyze(symbol, persist=False, track_paper=False))


class AnalyzeReq(BaseModel):
    symbol: str
    auto_sync: bool = False


@app.post(f"{api}/signals/analyze", dependencies=[Depends(auth)])
async def analyze(req: AnalyzeReq):
    symbol = _sym(req.symbol)

    def work():
        reports = [r.as_dict() for r in sy.sync_composed(symbol, history=True, live=True)] if req.auto_sync else []
        if req.auto_sync:
            newsmod.refresh_calendar()
        return {"analysis": engine.analyze(symbol), "sync": reports}
    return clean(await run_in_threadpool(work))


@app.get(f"{api}/signals", dependencies=[Depends(auth)])
def signals(symbol: str | None = None, limit: int = Query(100, le=1000), action: str | None = None):
    t = db.signals
    q = select(t).order_by(t.c.asof_ts.desc()).limit(limit)
    if symbol:
        q = q.where(t.c.symbol == _sym(symbol))
    if action:
        q = q.where(t.c.action == action.upper())
    with db.get_engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
    for r in rows:
        r["asof_utc"] = datetime.fromtimestamp(r["asof_ts"], timezone.utc).isoformat()
        p = r.pop("payload", {}) or {}
        r["reasons"] = p.get("reasons")
        r["display_action"] = p.get("display_action")
        r["regime_detail"] = p.get("vol_state")
        r["session"] = p.get("session")
        r["data_mode"] = p.get("data_mode")
    return clean(rows)


class BackfillReq(BaseModel):
    symbol: str | None = None
    days: int | None = None


@app.post(f"{api}/jobs/backfill", dependencies=[Depends(auth)])
def job_backfill(req: BackfillReq):
    syms = [_sym(req.symbol)] if req.symbol else list(SYMBOLS)

    def fn(log):
        out = []
        for s in syms:
            for r in sy.sync_composed(s, days=req.days):
                log(f"{r.symbol} {r.provider}: {r.status} fetched={r.fetched} last_bar={r.last_bar} gaps={len(r.gaps)} failed_days={len(r.failed_days)}")
                out.append(r.as_dict())
        newsmod.refresh_calendar()
        return out
    return {"job_id": jobs.submit("backfill", {"symbols": syms, "days": req.days}, fn)}


class TrainReq(BaseModel):
    symbol: str
    directions: bool = True


@app.post(f"{api}/jobs/train", dependencies=[Depends(auth)])
def job_train(req: TrainReq):
    symbol = _sym(req.symbol)

    def fn(log):
        rep = tr.train_symbol(symbol, directions=req.directions)
        t = rep["trade"]
        log(f"trade model status={t.get('status')} version={t.get('version')} events={t.get('events')}")
        return rep
    return {"job_id": jobs.submit("train", {"symbol": symbol}, fn)}


class BacktestReq(BaseModel):
    symbol: str
    walk_forward: bool = True


@app.post(f"{api}/jobs/backtest", dependencies=[Depends(auth)])
def job_backtest(req: BacktestReq):
    symbol = _sym(req.symbol)

    def fn(log):
        rep = bt.run_backtest(symbol, req.walk_forward)
        log(f"backtest {rep.get('status')} events={rep.get('events')} run={rep.get('run_id')}")
        return {k: rep.get(k) for k in ("status", "run_id", "events", "reason")}
    return {"job_id": jobs.submit("backtest", {"symbol": symbol, "walk_forward": req.walk_forward}, fn)}


@app.get(f"{api}/jobs", dependencies=[Depends(auth)])
def job_list(limit: int = 30):
    return clean(jobs.list_jobs(limit))


@app.get(f"{api}/jobs/{{job_id}}", dependencies=[Depends(auth)])
def job_get(job_id: str):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return clean(j)


@app.get(f"{api}/models", dependencies=[Depends(auth)])
def models(symbol: str | None = None):
    rows = registry.list_models(_sym(symbol) if symbol else None)
    for r in rows:
        r["health"] = registry.health(r) if r["status"] == "APPROVED" else None
    return clean(rows)


@app.post(f"{api}/models/{{version}}/approve", dependencies=[Depends(auth)])
def model_approve(version: str):
    try:
        return registry.approve(version, actor="dashboard")
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


class RollbackReq(BaseModel):
    symbol: str
    kind: str = "trade"


@app.post(f"{api}/models/rollback", dependencies=[Depends(auth)])
def model_rollback(req: RollbackReq):
    return registry.rollback(_sym(req.symbol), req.kind)


@app.get(f"{api}/calendar", dependencies=[Depends(auth)])
def calendar(symbol: str | None = None, hours: int = 72, refresh: bool = False):
    if refresh:
        newsmod.refresh_calendar()
    syms = [_sym(symbol)] if symbol else list(SYMBOLS)
    return clean({"state": newsmod.calendar_state(), "events": {s: newsmod.upcoming(s, hours=hours) for s in syms},
                  "blackout": {s: newsmod.blackout(s) for s in syms}, "policy": get_settings().news_unavailable_policy})


@app.get(f"{api}/paper-trades", dependencies=[Depends(auth)])
def paper_trades(symbol: str | None = None, limit: int = 200):
    return clean({"positions": journal.list_positions(limit, _sym(symbol) if symbol else None),
                  "authorized": journal.performance(True), "shadow": journal.performance(False), "circuit_breaker": journal.circuit_breaker()})


@app.get(f"{api}/backtests", dependencies=[Depends(auth)])
def backtests(symbol: str):
    r = bt.latest_backtest(_sym(symbol))
    return clean(r) if r else {"results": None}


@app.get(f"{api}/strategy-stats", dependencies=[Depends(auth)])
def strategy_stats():
    with db.get_engine().connect() as c:
        return clean([dict(r._mapping) for r in c.execute(select(db.strategy_stats))])


@app.get(f"{api}/audits", dependencies=[Depends(auth)])
def audits(limit: int = 50):
    with db.get_engine().connect() as c:
        return clean([dict(r._mapping) for r in c.execute(select(db.audits).order_by(db.audits.c.id.desc()).limit(limit))])


@app.post(f"{api}/monitor/{{action}}", dependencies=[Depends(auth)])
def monitor_ctl(action: str):
    if action == "start":
        monitor.start()
    elif action in ("pause", "stop"):
        monitor.pause()
    else:
        raise HTTPException(400, "action must be start|pause")
    return monitor.state


@app.get(f"{api}/monitor", dependencies=[Depends(auth)])
def monitor_state():
    return monitor.state


@app.get(f"{api}/stream", dependencies=[Depends(auth)])
async def stream(request: Request, last_event_id: str | None = Header(None)):
    """Server-sent events: snapshot on connect (so reconnecting browsers never miss state), then updates + heartbeats."""
    start = int(last_event_id) if (last_event_id or "").isdigit() else 0

    def latest():
        t = db.signals
        out = {}
        with db.get_engine().connect() as c:
            for s in SYMBOLS:
                r = c.execute(select(t.c.asof_ts, t.c.action, t.c.strength, t.c.bias, t.c.feed_status, t.c.blockers, t.c.id).where(t.c.symbol == s)
                              .order_by(t.c.asof_ts.desc()).limit(1)).first()
                out[s] = None if r is None else dict(r._mapping)
        return clean({"signals": out, "monitor": monitor.state, "server_time": datetime.now(timezone.utc).isoformat()})

    async def gen():
        n, last = start, None
        while not await request.is_disconnected():
            snap = await run_in_threadpool(latest)
            key = json.dumps(snap["signals"], sort_keys=True)
            if key != last or n == start:
                n += 1
                last = key
                yield f"id: {n}\nevent: snapshot\ndata: {json.dumps(snap)}\n\n"
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(STREAM_INTERVAL)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
