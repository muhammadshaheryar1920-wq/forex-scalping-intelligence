# Database schema (SQLAlchemy Core, `forexintel/db.py`)

SQLite by default (`data/forex.db`, WAL mode). Set `DATABASE_URL=postgresql+psycopg://user:pass@host/db` (add `psycopg[binary]` to requirements) for PostgreSQL;
tables are created by `db.init_db()` and additive column changes by `_migrate()`. All timestamps are UTC epoch seconds.

| Table | Key fields / constraints |
|---|---|
| `instruments` | symbol (PK), provider_symbols (JSON), quote_precision, tick_size, pip_size, contract_size |
| `candles` | PK (provider, symbol, timeframe, open_ts); mid OHLC + bid/ask OHLC (`bo..ac`), volume, `has_bid_ask`, `is_complete`, `source_hash`, `received_ts`. Only closed bars are stored. Base timeframe M5. |
| `quotes` | provider, symbol, source_ts, received_ts, bid, ask, spread, status; UNIQUE (provider, symbol, source_ts) (reserved for a streaming adapter) |
| `sync_state` | PK (provider, symbol, timeframe): last complete bar, last sync time, status, detail (gaps, failed days) |
| `economic_events` | event_id (PK), currency, title, scheduled_ts, impact, forecast/previous/actual, `published_ts` (when WE fetched it = point-in-time knowledge), source |
| `signals` | id (PK); UNIQUE (symbol, asof_ts, provider) so one analysis per bar; forecast bias/prob, action, strength, strategy, plan, blockers, full JSON payload, model_version, state |
| `strategy_candidates` | every candidate per signal (selected or not), plan JSON, reasons, rank |
| `models` | version (PK), symbol, kind (`trade`, `direction_h3/6/12`), algo, dataset_hash, train_end_ts, feature_version, metrics (full training report), status (`CANDIDATE/REJECTED/APPROVED/ARCHIVED`), artifact_path, checksum, rollback_to |
| `backtest_runs` | id, symbol, params, results (full JSON) |
| `strategy_stats` | PK (symbol, strategy): n, win rate + Wilson CI, net EV, EV CI, PF, max DD, detail (halves, hold-time quantiles); consumed by the validated-edge gate |
| `paper_positions` | id = `signal_id:strategy:L\|S` (PK, idempotent); authorized vs shadow; state `PENDING/OPEN/CLOSED/NOFILL`; fills, SL/TPs, units, risk, exit, net R, P&L, fees |
| `jobs` | id, type, status, params, log, result |
| `audits` | ts, actor, event, detail |

Not implemented from the spec: `feature_snapshots` (features are recomputed deterministically from stored candles; the signal payload and dataset hash provide lineage) and a `users` table (single-user, loopback, optional API token).
