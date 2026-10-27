# HTTP API (`/api/v1`, Swagger at `/docs`)

If `API_TOKEN` is set, send `Authorization: Bearer <token>` or `X-API-Key: <token>` (the SSE stream also accepts `?token=`). `GET /health` stays open.

| Method / path | Purpose |
|---|---|
| `GET /health` | liveness, DB, providers, monitor state |
| `GET /providers/health?deep=` | provider health and capabilities |
| `GET /settings` | non-secret settings; credentials shown only as set / not set |
| `GET /markets/{symbol}/snapshot` | last bar, bid/ask/spread (flagged if assumed), data quality, sync state, lineage |
| `GET /markets/{symbol}/candles?timeframe=M5\|M15\|H1&limit=` | closed candles + overlays (BOS/CHoCH/sweeps/FVG/OB/levels) |
| `GET /markets/{symbol}/analysis` | latest analysis, read-only (not persisted) |
| `POST /signals/analyze {symbol, auto_sync}` | analysis that is persisted (idempotent per bar); optional sync first |
| `GET /signals?symbol=&action=&limit=` | stored analyses including `NO_TRADE` |
| `POST /jobs/backfill`, `/jobs/train`, `/jobs/backtest` | background jobs with real IDs |
| `GET /jobs`, `GET /jobs/{id}` | status, log, result |
| `GET /models`, `POST /models/{version}/approve`, `POST /models/rollback` | model lifecycle |
| `GET /calendar?symbol=&refresh=` | events, blackout state, calendar status |
| `GET /paper-trades` | positions, authorized vs shadow performance, circuit breaker |
| `GET /backtests?symbol=`, `GET /strategy-stats` | latest backtest, per-strategy evidence used by the gate |
| `GET /monitor`, `POST /monitor/start\|pause` | in-process M5 monitor (runs only while the server runs) |
| `GET /stream` | SSE: `snapshot` events with ids, `: heartbeat` comments; a reconnecting client gets a fresh snapshot |
| `GET /audits` | audit trail |

## Analysis result (abridged)

```json
{
  "symbol": "XAUUSD", "asof_utc": "2026-10-27T13:55:00+00:00", "feed_status": "HEALTHY", "data_mode": "LIVE",
  "regime": "TREND_UP", "vol_state": "NORMAL", "session": "LONDON_NY_OVERLAP",
  "directional_forecast": {"bias": "UP", "horizon_m5_bars": 6, "probability": null, "status": "NOT_CALIBRATED"},
  "trade_action": "NO_TRADE", "display_action": "WAIT", "strength": "NONE",
  "candidate_strategy": "TREND_CONTINUATION", "entry_zone": null, "sl": null, "tp1": null,
  "blockers": ["NO_VALIDATED_EDGE"], "reasons": ["..."], "candidates": [ "... every candidate, including rejected ..." ],
  "provider": "dukascopy+oanda", "model_version": null
}
```

`trade_action`: `LONG | SHORT | NO_TRADE | CANNOT_ASSESS`. Plans (`entry_zone, sl, tp1..3, rr, expected_move_pct, holding_minutes, valid_until_utc`) appear only when authorized.
