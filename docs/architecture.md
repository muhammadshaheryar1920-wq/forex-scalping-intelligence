# Architecture decision record - local edition

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Python 3.11 package `forexintel` (not a multi-service monorepo yet) | One reusable engine; `strategy`, `research`, `providers` have no web/CLI imports, so a cloud worker or API can import them unchanged. |
| 2 | SQLite by default through SQLAlchemy Core; `DATABASE_URL` accepts PostgreSQL | Docker is not installed on the target laptop; the schema avoids SQLite-only features and `db.upsert` branches by dialect. A tiny additive `_migrate()` adds new columns to existing files. |
| 3 | Store **M5 only** as the base series; M15/H1 are deterministic aggregates of closed M5 bars built on load | Avoids three tables that can disagree; a higher-timeframe bar is emitted only after its end time has passed (no forming bar can leak). Deviation from the spec's three stored timeframes. |
| 4 | Providers are adapters behind `MarketDataProvider`; series are stored per provider and composed at load time (`history` + optional `live` tail) | Switching sources never rewrites the engine. Proxy series (e.g. a futures contract) are never stitched with spot history (`spot_compatible`). |
| 5 | Always bid/ask OHLC columns; mid-only providers get an *assumed* symmetric spread flagged `has_bid_ask=False` | One simulation path; the UI and signal always say when the spread is not real. |
| 6 | Strategies are vectorised trigger generators; one scalar `make_plan` and one `simulate` serve live analysis, labels, backtests and the paper journal | Replay determinism and identical fill rules everywhere. |
| 7 | Trade authorization needs *evidence*: calibrated ML (if approved and healthy) or a rules backtest with n >= 50, net EV >= 0.05 R and positive in both chronological halves | Prevents "forced BUY/SELL". Unvalidated candidates are shown as `WAIT` with `NO_VALIDATED_EDGE` and tracked as *shadow* paper trades to gather forward evidence. |
| 8 | Dashboard is a dependency-light single-page app served by FastAPI (vanilla JS, `lightweight-charts` from a pinned CDN URL) | Zero build tooling on the laptop; the same REST/SSE API can later back a Next.js front end. Deviation from the spec's Next.js requirement. |
| 9 | Human approval gate for every model; checksum-verified artifacts; rollback pointer | Spec 7.1 step 16. |

## Data flow

```
provider (Dukascopy / Yahoo / OANDA ...) -> sync (checkpoint, overlap, dedupe, gap report) -> candles table (closed M5 bars)
   -> load_series (history + live tail) -> quality gate (HEALTHY / STALE / DEGRADED / MARKET_CLOSED / INSUFFICIENT_DATA)
   -> build_features (M5 decision bar + closed M15/H1 context) -> regime -> strategies -> plans
   -> evidence (ML bundle or rules backtest stats) -> hard gates (data, spread, news, exposure, circuit breaker)
   -> signal row + candidate log + paper position (idempotent ids) -> API / CLI / dashboard
```

## Idempotency and restarts

- `signals` is unique on `(symbol, asof_ts, provider)`; paper positions use `signal_id:strategy:side` as primary key.
- Re-analysing the same closed bar returns the same decision (tested), and exposure checks ignore the signal's own position.
- `sync` resumes from the last saved bar minus a 2 h overlap and re-requests previously unfilled gaps.

## Porting to the cloud edition

Move `forexintel.strategy`, `forexintel.research`, `forexintel.providers` as-is. Replace SQLite with PostgreSQL via
`DATABASE_URL`, run `monitor` as an always-on worker (single-leader lock on `(symbol, asof_ts, provider)` already
exists as a unique constraint), keep model artifacts on a persistent volume or object storage (Railway disks are
ephemeral), and serve the same `/api/v1` from the API service. See [deployment-local.md](deployment-local.md).
