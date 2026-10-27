#!/usr/bin/env python
"""Forex scalping intelligence platform - local CLI.  PAPER TRADING / RESEARCH ONLY: never places real orders.

Exit codes: 0 success | 1 error | 2 data blocked / cannot assess (stale feed, missing credentials, insufficient data)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forexintel.config import INSTRUMENTS, SYMBOLS, get_settings, normalize_symbol  # noqa: E402

LINE = "-" * 78


def _symbols(args) -> list[str]:
    if getattr(args, "all", False) or not getattr(args, "symbol", None):
        return list(SYMBOLS)
    return [normalize_symbol(s) for s in args.symbol.split(",")]


def _print_sync(r) -> None:
    d = r.as_dict() if hasattr(r, "as_dict") else r
    print(f"  {d['symbol']:7s} {d['provider']:10s} {d['status']:11s} fetched={d['fetched']:<6} stored={d['stored_total']:<7} "
          f"last_bar={d['last_bar'] or '-'}  freshness={d['freshness_minutes']} min")
    if d["requested_from"]:
        print(f"          requested {d['requested_from'][:16]} -> {d['requested_to'][:16]}")
    for n in d["notes"]:
        print(f"          note: {n}")
    if d["gaps"]:
        tot = sum(g["bars"] for g in d["gaps"])
        print(f"          gaps: {len(d['gaps'])} interval(s), {tot} expected bars missing (e.g. holidays/outages); first: "
              f"{d['gaps'][0]['start'][:16]} ({d['gaps'][0]['bars']} bars)")
    if d["failed_days"]:
        print(f"          failed day files (retried next sync): {', '.join(d['failed_days'][:8])}")
    if d["error"]:
        print(f"          ERROR: {d['error']}")


def cmd_doctor(args) -> int:
    import importlib
    from forexintel import db
    from forexintel.data import store
    from forexintel.features import news
    from forexintel.providers import PROVIDER_NAMES, get_provider
    from forexintel.research import registry
    fails = warns = 0

    def out(level, msg):
        nonlocal fails, warns
        fails += level == "FAIL"
        warns += level == "WARN"
        print(f"[{level:4s}] {msg}")
    print(LINE)
    out("PASS" if sys.version_info >= (3, 10) else "FAIL", f"python {sys.version.split()[0]}")
    for mod in ("numpy", "pandas", "sklearn", "lightgbm", "xgboost", "sqlalchemy", "fastapi", "uvicorn", "requests", "joblib"):
        try:
            m = importlib.import_module(mod)
            out("PASS", f"{mod} {getattr(m, '__version__', '')}")
        except ImportError:
            out("FAIL", f"{mod} missing: pip install -r requirements.txt")
    s = get_settings()
    try:
        db.init_db()
        db.seed_instruments()
        out("PASS", f"database ready ({s.database_url.split('://')[0]})")
    except Exception as e:  # noqa: BLE001
        out("FAIL", f"database: {e}")
    out("PASS", f"history provider: {s.history_provider}; live provider: {s.live_provider}")
    for p in {s.history_provider, s.live_provider} - {"none", ""}:
        try:
            h = get_provider(p).health() if (args.deep or p in ("oanda", "twelvedata", "mt5", "demo")) else {"ok": True, "note": "skipped (use --deep for a network test)"}
        except Exception as e:  # noqa: BLE001
            h = {"ok": False, "detail": str(e)}
        lvl = "PASS" if h.get("ok") else ("WARN" if h.get("blocked") else "FAIL")
        out(lvl, f"provider {p}: {json.dumps({k: v for k, v in h.items() if k != 'provider'})}")
    print("symbol coverage (history provider):")
    try:
        prov = get_provider(s.history_provider)
    except Exception as e:  # noqa: BLE001
        out("FAIL", f"history provider '{s.history_provider}': {e}")
        print(LINE)
        print(f"doctor: {fails} failure(s), {warns} warning(s)")
        return 1
    for sym in SYMBOLS:
        out("PASS" if prov.supports(sym) else "FAIL", f"{sym}: {'supported' if prov.supports(sym) else 'NOT supported by ' + s.history_provider}")
    cap = prov.capabilities()
    out("PASS" if cap["bid_ask"] else "WARN", f"bid/ask: {'real' if cap['bid_ask'] else 'NO - mid only, spread assumed'}; verified by this project: {cap['verified']}")
    for sym in SYMBOLS:
        last = store.last_complete_ts(s.history_provider, sym)
        out("PASS" if last is not None else "WARN", f"{sym} stored candles: last={last.isoformat() if last is not None else 'none - run sync'}")
    cs = news.calendar_state()
    out("PASS" if cs["status"] == "OK" else "WARN", f"economic calendar: {cs['status']} (policy {s.news_unavailable_policy})")
    for sym in SYMBOLS:
        h = registry.health(registry.active_model(sym, "trade"))
        out("PASS" if h["state"] in ("OK",) else "WARN", f"{sym} trade model: {h['state']}{' - rules-only baseline' if h['state'] == 'NO_MODEL' else ''}")
    if s.api_token:
        out("PASS", "API token configured")
    else:
        out("WARN", "API_TOKEN not set: API is loopback-only (127.0.0.1) without auth; do not expose it")
    print(LINE)
    print(f"doctor: {fails} failure(s), {warns} warning(s)")
    return 1 if fails else 0


def cmd_sync(args) -> int:
    from forexintel.data import sync as sy
    from forexintel.features import news
    if args.timeframes and set(args.timeframes.upper().split(",")) - {"M5"}:
        print("note: M5 is the stored base timeframe; M15/H1 are deterministic aggregates of CLOSED M5 bars built on load.")
    print(LINE)
    blocked = False
    for sym in _symbols(args):
        if args.provider:
            reps = [sy.sync_symbol(sym, args.provider, days=args.days)]
        else:
            reps = sy.sync_composed(sym, days=args.days)
        for r in reps:
            _print_sync(r)
            blocked |= r.status in ("BLOCKED", "FAILED", "UNSUPPORTED")
    cal = news.refresh_calendar()
    print(f"  calendar: {'refreshed ' + str(cal['events']) + ' events' if cal['ok'] else 'UNAVAILABLE (' + str(cal.get('error')) + ')'}")
    return 2 if blocked else 0


def format_signal(r: dict) -> str:
    L = [LINE, f"{r['display']}  as of {r.get('asof_utc')}  |  feed {r['feed_status']}  data={r.get('data_mode')}  provider={r['provider']}"]
    q = r["data_quality"]
    L.append(f"data: last bar {q.get('last_bar_open')}, age {q.get('age_minutes')} min, bars {q.get('bars')}, recent gaps {q.get('recent_missing_bars')}")
    if r["trade_action"] == "CANNOT_ASSESS":
        L += [f"RESULT: CANNOT ASSESS - {', '.join(r['blockers'])}"] + [f"  {n}" for n in q.get("notes", [])]
        return "\n".join(L)
    f = r["directional_forecast"]
    prob = f"{f['probability']:.2f}" if f.get("probability") is not None else "n/a"
    L.append(f"regime: {r['regime']} ({r['vol_state']} vol)   session: {r['session']}   price: bid {r['price']['bid']} / ask {r['price']['ask']} "
             f"(spread {r['price']['spread']:.5g}{'' if r['price']['spread_is_real'] else ', ASSUMED'})")
    L.append(f"DIRECTIONAL FORECAST: {f['bias']} over next {f['horizon_m5_bars']} M5 bars  P(up)={prob} [{f['status']}]  via {f['method']}")
    L.append(f"TRADE DECISION: {r['display_action']}  ({r['trade_action']}, strength {r['strength']})")
    if r["trade_action"] in ("LONG", "SHORT"):
        L.append(f"  strategy {r['candidate_strategy']}   entry zone {r['entry_zone']}   SL {r['sl']}")
        L.append(f"  TP1 {r['tp1']}  TP2 {r['tp2']}  TP3 {r['tp3']}   R:R {r['rr']}   expected move % {r['expected_move_pct']}")
        p = r["probability"]
        L.append(f"  probability: {p.get('value') if p.get('value') is None else round(p['value'], 3)} [{p['status']}]   net EV {r['expected_value_r']}R   "
                 f"holding ~{r['holding_minutes'][0]}-{r['holding_minutes'][1]} min   valid until {r['valid_until_utc']}")
    elif r.get("candidate_strategy"):
        pp = r.get("plan_preview") or {}
        L.append(f"  research candidate (NOT authorized): {r['candidate_strategy']} entry~{pp.get('entry_ref')} SL {pp.get('sl')} TP1 {pp.get('tp1')}")
    if r["blockers"]:
        L.append(f"  blockers: {', '.join(r['blockers'])}")
    if r["warnings"]:
        L.append(f"  warnings: {', '.join(r['warnings'])}")
    for x in r.get("reasons", [])[:5]:
        L.append(f"  - {x}")
    n = r.get("news") or {}
    if n.get("events"):
        L.append("  news: " + "; ".join(f"{e['currency']} {e['title']} in {e['minutes_to']} min" for e in n["events"]))
    elif n.get("state") == "UNAVAILABLE":
        L.append("  news: CALENDAR_UNAVAILABLE")
    return "\n".join(L)


def cmd_signal(args) -> int:
    from forexintel.data import sync as sy
    from forexintel.features import news
    from forexintel.strategy import engine
    rc = 0
    for sym in _symbols(args):
        if args.auto_sync:
            print(f"[auto-sync] {sym}")
            for r in sy.sync_composed(sym):
                _print_sync(r)
            news.refresh_calendar()
        res = engine.analyze(sym, persist=not args.no_persist)
        if args.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            print(format_signal(res))
        if res["trade_action"] == "CANNOT_ASSESS" or res["feed_status"] in ("STALE", "DEGRADED", "NO_DATA", "INSUFFICIENT_DATA"):
            rc = max(rc, 2)
    return rc


def cmd_train(args) -> int:
    from forexintel.research import train as tr
    if args.strategy != "all":
        print("per-strategy training is not supported: the meta-ranker is trained on all strategies' candidates; "
              "use `backtest` to compare strategies individually.")
        return 1
    algos = tuple(args.algos.split(","))
    for sym in _symbols(args):
        print(f"{LINE}\ntraining {sym} ...")
        rep = tr.train_symbol(sym, algos, directions=not args.no_direction)
        t = rep["trade"]
        print(f"trade model: status={t.get('status')} chosen={t.get('chosen')} version={t.get('version')} events={t.get('events')}")
        if t.get("reason"):
            print(f"  reason: {t['reason']}")
        if t.get("split_sizes"):
            print(f"  split sizes {t['split_sizes']}, test base rate vs train {t.get('base_rate_train'):.3f}")
        bt = (t.get("baseline_test") or {}).get("pooled_rules_only")
        if bt and bt.get("n"):
            print(f"  rules-only baseline (test): n={bt['n']} win={bt['win_rate']:.1%} net EV={bt['ev_r']:+.3f}R PF={bt['pf']}")
        ts = t.get("test_selected")
        if ts and ts.get("n"):
            print(f"  ML-selected (test, frozen): n={ts['n']} win={ts['win_rate']:.1%} [{ts['wr_lo']:.1%},{ts['wr_hi']:.1%}] net EV={ts['ev_r']:+.3f}R PF={ts['pf']} {t.get('note', '')}")
        elif t.get("chosen"):
            print("  ML-selected (test): no trades selected")
        pm = t.get("test_prob_metrics")
        if pm and pm.get("auc") is not None:
            print(f"  test calibration: Brier={pm['brier']:.4f} logloss={pm['logloss']:.4f} AUC={pm['auc']:.3f}")
        for h, d in (rep.get("direction", {}).get("horizons") or {}).items():
            if d.get("balanced_accuracy") is not None:
                print(f"  direction h={h}: bal.acc={d['balanced_accuracy']:.3f} (lower CI {d['ba_lo']:.3f}) logloss={d['test']['logloss']:.4f} "
                      f"passes={d['passes']} version={d.get('version')}")
        print("  models are never auto-activated: review, then `python main.py model-approve <version>`")
    return 0


def cmd_backtest(args) -> int:
    from forexintel.research import backtest as bt
    for sym in _symbols(args):
        print(f"{LINE}\nbacktest {sym} (walk-forward={'on' if args.walk_forward else 'off'}) ...")
        r = bt.run_backtest(sym, walk_forward_on=args.walk_forward)
        if r["status"] != "DONE":
            print(f"  {r['status']}: {r.get('reason')}")
            continue
        print(f"  data {r['data_from'][:10]} -> {r['data_to'][:10]}, {r['bars']} bars, {r['events']} candidate trades, costs: {r['costs']['spread']}")
        p = r["pooled_rules_only"]
        print(f"  POOLED rules-only (1 position at a time): n={p['n']} win={p['win_rate']:.1%} [{p['wr_lo']:.1%},{p['wr_hi']:.1%}] "
              f"net EV={p['ev_r']:+.3f}R [{p['ev_lo']:+.3f},{p['ev_hi']:+.3f}] PF={p['pf']:.2f} maxDD={p['max_dd_r']:.1f}R")
        for k, d in r["per_strategy_rules_only"].items():
            print(f"    {k:26s} n={d['n']:<4} win={d['win_rate']:.1%} EV={d['ev_r']:+.3f}R PF={d['pf'] if d['pf'] is None else round(d['pf'], 2)} "
                  f"halves+={d['halves_positive']} TP2 reach={d['tp2_reach_rate']:.0%}")
        wf = r.get("walk_forward")
        if wf and "models" in wf:
            b = wf["baseline_oos"]
            print(f"  WALK-FORWARD OOS baseline: n={b['n']} EV={b['ev_r'] if b['ev_r'] is None else round(b['ev_r'], 3)}R")
            for a, m in wf["models"].items():
                o = m["oos_selected"]
                print(f"    {a:9s} selected n={o['n']} win={'-' if o['win_rate'] is None else format(o['win_rate'], '.1%')} "
                      f"EV={'-' if o['ev_r'] is None else format(o['ev_r'], '+.3f')}R positive folds {m['folds_positive']}/{len(m['folds'])}")
        print(f"  run id {r['run_id']}; strategy stats updated (used by the signal engine as the validated-edge gate)")
    return 0


def cmd_monitor(args) -> int:
    from forexintel.data import store, sync as sy
    from forexintel.features import news
    from forexintel.paper import journal
    from forexintel.strategy import engine
    syms = _symbols(args)
    print(f"monitoring {', '.join(syms)} on M5 candle close (Ctrl+C to stop). Only runs while this machine is on; "
          f"missed periods are back-filled on the next sync, ticks while offline are NOT recoverable.")
    news.refresh_calendar()
    try:
        while True:
            now = datetime.now(timezone.utc)
            for sym in syms:
                for r in sy.sync_composed(sym, history=args.history, live=True):
                    if r.status not in ("OK", "UNSUPPORTED"):
                        print(f"[{now:%H:%M:%S}] {sym} sync {r.status}: {r.error or r.notes}")
                res = engine.analyze(sym)
                m5, _ = store.load_series(sym)
                adv = journal.advance(sym, m5)
                print(f"[{now:%H:%M:%S}] {sym} feed={res['feed_status']} bias={res['directional_forecast'].get('bias')} "
                      f"action={res['display_action']} regime={res.get('regime')} blockers={res['blockers']} paper={adv}")
            if args.once:
                return 0
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)) + timedelta(seconds=args.lag)
            time.sleep(max(5, (nxt - datetime.now(timezone.utc)).total_seconds()))
    except KeyboardInterrupt:
        print("\nmonitor stopped")
        return 0


def cmd_model_status(args) -> int:
    from forexintel.research import registry
    print(LINE)
    for sym in _symbols(args):
        act = registry.active_model(sym, "trade")
        h = registry.health(act)
        print(f"{sym}: trade model {h['state']}  {act['version'] if act else '(rules-only baseline)'}  {h.get('flags') or ''}")
        for m in registry.list_models(sym)[:8]:
            met = m["metrics"] or {}
            ts = (met.get("test_selected") or {})
            extra = f" test n={ts.get('n')} EV={ts.get('ev_r')}" if ts else ""
            print(f"    {m['status']:10s} {m['version']}  [{m['kind']}]{extra}")
    return 0


def cmd_model_approve(args) -> int:
    from forexintel.research import registry
    print(json.dumps(registry.approve(args.version), indent=2))
    return 0


def cmd_model_rollback(args) -> int:
    from forexintel.research import registry
    print(json.dumps(registry.rollback(normalize_symbol(args.symbol), args.kind), indent=2))
    return 0


def cmd_paper(args) -> int:
    from forexintel.paper import journal
    for label, auth in (("AUTHORIZED signals", True), ("SHADOW research candidates (no validated edge)", False)):
        p = journal.performance(auth)
        print(f"{label}: closed={p['n']} win={p['win_rate']} EV={p['ev_r']}R PF={p['pf']} maxDD={p['max_dd_r']}R open={p['open']}")
    for pos in journal.list_positions(15):
        print(f"  {pos['state']:8s} {pos['symbol']} {pos['side']:5s} {pos['strategy']:24s} auth={pos['authorized']} net_r={pos['net_r']} {pos['exit_reason'] or ''}")
    return 0


def cmd_calendar(args) -> int:
    from forexintel.features import news
    print(news.refresh_calendar())
    for sym in SYMBOLS:
        for e in news.upcoming(sym, hours=72, impacts=("High",))[:5]:
            print(f"  {sym} {e['scheduled_utc'][:16]} {e['currency']} {e['title']} ({e['impact']}) in {e['minutes_to']} min")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    if args.host not in ("127.0.0.1", "localhost") and not get_settings().api_token:
        print("refusing to bind a non-loopback host without API_TOKEN set (see .env.example)")
        return 1
    uvicorn.run("forexintel.api.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_dashboard(args) -> int:
    import threading
    import webbrowser
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    args.host = "127.0.0.1"
    return cmd_serve(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="main.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def sym(sp):
        sp.add_argument("--symbol", help="XAUUSD | EURUSD | GBPUSD (comma-separated allowed)")
        sp.add_argument("--all", action="store_true", help="all three instruments")

    sp = sub.add_parser("doctor", help="environment / credentials / data / model health check")
    sp.add_argument("--deep", action="store_true", help="also test provider network reachability")
    sp.set_defaults(fn=cmd_doctor)
    for name in ("sync", "fetch"):
        sp = sub.add_parser(name, help="fetch missing candles (catch-up) and validate gaps" if name == "sync" else "alias of sync")
        sym(sp)
        sp.add_argument("--timeframes", default="M5,M15,H1")
        sp.add_argument("--catch-up", action="store_true", help="(default behaviour) resume from the last saved candle")
        sp.add_argument("--days", type=int, help="history depth for a first sync")
        sp.add_argument("--provider", help="sync a single provider instead of history+live")
        sp.set_defaults(fn=cmd_sync)
    sp = sub.add_parser("signal", help="latest analysis: directional forecast + trade decision")
    sym(sp)
    sp.add_argument("--auto-sync", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-persist", action="store_true")
    sp.set_defaults(fn=cmd_signal)
    sp = sub.add_parser("train", help="train/evaluate/register models (never auto-approved)")
    sym(sp)
    sp.add_argument("--strategy", default="all")
    sp.add_argument("--algos", default="logistic,lightgbm,xgboost")
    sp.add_argument("--no-direction", action="store_true")
    sp.set_defaults(fn=cmd_train)
    sp = sub.add_parser("backtest", help="cost-realistic backtest, per-strategy stats and walk-forward")
    sym(sp)
    sp.add_argument("--walk-forward", action="store_true")
    sp.set_defaults(fn=cmd_backtest)
    sp = sub.add_parser("monitor", help="analyze on every M5 close while the machine is on")
    sym(sp)
    sp.add_argument("--on-candle-close", action="store_true", help="(default) wake on M5 closes")
    sp.add_argument("--once", action="store_true")
    sp.add_argument("--lag", type=int, default=20, help="seconds to wait after the close before fetching")
    sp.add_argument("--history", action="store_true", help="also re-sync the history provider each cycle")
    sp.set_defaults(fn=cmd_monitor)
    sp = sub.add_parser("model-status", help="show active/candidate models and health")
    sym(sp)
    sp.set_defaults(fn=cmd_model_status)
    sp = sub.add_parser("model-approve", help="human approval of a CANDIDATE model version")
    sp.add_argument("version")
    sp.set_defaults(fn=cmd_model_approve)
    sp = sub.add_parser("model-rollback", help="deactivate the active model (restore previous or rules-only)")
    sp.add_argument("--symbol", required=True)
    sp.add_argument("--kind", default="trade")
    sp.set_defaults(fn=cmd_model_rollback)
    sub.add_parser("paper", help="paper-trading journal summary").set_defaults(fn=cmd_paper)
    sub.add_parser("calendar", help="refresh + show upcoming high-impact events").set_defaults(fn=cmd_calendar)
    for name in ("serve", "dashboard"):
        sp = sub.add_parser(name, help="FastAPI + dashboard on localhost" if name == "serve" else "serve and open the browser")
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=8000)
        sp.set_defaults(fn=cmd_serve if name == "serve" else cmd_dashboard)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        if "--debug" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
