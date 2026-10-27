# Forex Scalping Intelligence (local edition, paper trading only)

Adaptive, explainable research platform for **XAU/USD, EUR/USD, GBP/USD** on M5 entries with M15/H1 context.
It never places real orders. Every request returns a **directional forecast** and, separately, a **trade decision**
(`LONG / SHORT / NO_TRADE`, shown as `STRONG BUY / BUY / WAIT / SELL / STRONG SELL`). A trade is only published when
a strategy has *measured, positive, cost-adjusted* evidence behind it. No forced signals, no invented probabilities.

> **Read this first - current evidence.** On 3-12 months of real Dukascopy bid/ask data with realistic spread,
> slippage and commission, none of the four baseline strategies and none of the trained models showed a positive
> net expectancy out of sample (zero gross edge, about -0.12 to -0.30 R per trade after costs), and the directional
> models score about 50% balanced accuracy. The bot therefore answers **WAIT / NO_TRADE with an explicit blocker**.
> That is the system working as designed, not a bug. A 70-80% win rate is an aspiration, not something this
> software can promise. See [docs/model-validation.md](docs/model-validation.md) for numbers and how to keep researching.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env            # optional: add free keys later; nothing is required to start
python main.py doctor             # environment, providers, data, models
python main.py sync --all --catch-up --days 90     # free Dukascopy bid/ask history (first run takes a few minutes)
python main.py backtest --all --walk-forward       # honest per-strategy and walk-forward statistics
python main.py train --symbol XAUUSD               # train + validate (never auto-activated)
python main.py signal --symbol XAUUSD --auto-sync
python main.py dashboard                            # http://127.0.0.1:8000
```

## Commands

| Command | What it does |
|---|---|
| `doctor [--deep]` | Checks Python, packages, DB, providers, symbol coverage, stored data, calendar, models. Exit 1 on failures. |
| `sync --symbol X \| --all [--catch-up] [--days N] [--provider P]` | Finds the last saved candle, fetches the missing span (with overlap), validates gaps/duplicates, stores closed bars only, prints recovered range, gaps and freshness. `--days` deeper than stored history back-fills older data. `fetch` is an alias. |
| `signal --symbol X \| --all [--auto-sync] [--json]` | Latest analysis. `--auto-sync` syncs first, then analyses; stale/missing data gives `CANNOT_ASSESS` or a `FEED_STALE` blocker instead of a guess. Exit 2 when data is blocked. |
| `train --symbol X [--algos logistic,lightgbm,xgboost]` | Builds point-in-time features and labels, chronological split with purge/embargo, compares models on validation only, evaluates the frozen model once on the untouched test slice, calibrates, ablates, registers a versioned artifact. |
| `backtest --symbol X [--walk-forward]` | Cost-realistic backtest, per-strategy stats (feeds the validated-edge gate), session/regime/month breakdown, expanding-window walk-forward for ML models. |
| `monitor --all --on-candle-close` | Sync + analyse + advance paper trades after every M5 close, while the machine is on. Missed periods are back-filled later; ticks while offline are not recoverable. |
| `model-status`, `model-approve <version>`, `model-rollback --symbol X` | Human-gated model lifecycle. Rejected models cannot be approved. Rollback restores the previous model or the rules-only baseline. |
| `paper`, `calendar` | Paper journal summary; refresh/show upcoming high-impact events. |
| `serve [--host --port]`, `dashboard` | FastAPI + 8-page dashboard on `127.0.0.1:8000` (Swagger at `/docs`). |

Exit codes: `0` ok, `1` error, `2` data blocked / cannot assess.

## Data sources (all free)

| Role | Source | Notes |
|---|---|---|
| History / training (default) | **Dukascopy** public datafeed | Real **bid and ask** M1 candles aggregated to M5, XAU/USD + EUR/USD + GBP/USD, years deep, about 1 day lag. Verified. |
| Live tail (default) | **Yahoo Finance** chart API | EUR/USD and GBP/USD only (no spot gold). **Mid prices only**: spread is *assumed* and labelled so. Unofficial endpoint. |
| Optional | **OANDA v20** practice token | Real bid/ask + live pricing for all three. Adapter written from the docs; **not verified** without your token. |
| Optional | Twelve Data free key, MT5 | Adapters present, unverified, mid-only (Twelve Data) / Windows terminal (MT5). |
| Calendar | Forex Factory weekly JSON mirror | Unofficial, terms unverified, current week only. If unreachable the status is `CALENDAR_UNAVAILABLE` (policy `warn` or `block`). |

For a real-time **gold** feed you need a free OANDA practice token (see [docs/provider-selection.md](docs/provider-selection.md)):
put `OANDA_TOKEN`, `OANDA_ACCOUNT_ID` in `.env`, set `HISTORY_PROVIDER=oanda` (or keep Dukascopy history and set `FOREX_PROVIDER=oanda`).
Without a live gold source the XAU/USD signal is honestly marked `FEED_STALE` (history lags about a day).

## Project layout

```
main.py                    CLI
forexintel/
  config.py db.py          settings, instrument specs, SQLAlchemy schema (SQLite; PostgreSQL-ready)
  providers/               dukascopy, yahoo, oanda, twelvedata, mt5, demo (synthetic, labelled DEMO)
  data/                    calendar (market hours/gaps), store (candles, M15/H1 resampling), sync, quality gate
  features/                indicators, structure (swings/BOS/CHoCH/sweeps/FVG/OB), sessions, news, builder, overlays
  strategy/                regime, 4 strategies, plan (SL/TP/costs), risk gates, engine (analysis contract)
  research/                simulate, dataset (events+labels), models, train, backtest, registry, metrics
  paper/journal.py         idempotent paper positions, circuit breaker, exposure rules
  api/                     FastAPI app, jobs, static dashboard (no build step)
tests/                     74 automated tests (unit, temporal/leakage, provider, sync, decision, ML, paper, API, CLI)
docs/                      architecture, providers, algorithms, validation, risk, API, schema, security, runbook, deployment
models/  data/             versioned model artifacts, SQLite DB and cached day files (git-ignored)
```

## Tests

```powershell
python -m pytest tests -q        # about 5 minutes; no network needed
```

## Safety statements

- Paper trading and research only. No broker order code exists in this repository.
- Probabilities are shown only when calibrated on an untouched slice; otherwise `NOT_CALIBRATED` / `null`.
- Only data-quality, cost/spread, news, exposure and risk-limit conditions can veto a trade; a single RSI/MACD/SMC
  mismatch never does (covered by tests).
- Not financial advice.
