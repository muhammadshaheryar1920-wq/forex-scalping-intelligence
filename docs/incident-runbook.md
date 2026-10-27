# Incident runbook

| Symptom | Meaning | Action |
|---|---|---|
| `FEED_STALE` / badge DELAYED | last complete M5 bar is more than 3 expected bars behind | `python main.py sync --all`. With Dukascopy-only history, ~1 day lag is normal: add a live provider (OANDA practice token, or Yahoo for EUR/GBP). |
| `FEED_DEGRADED` | more than 3 M5 bars missing in the last 200 | `sync --days N` re-requests unfilled gaps; check `failed_days` in the report (Dukascopy 503 throttling clears on retry). Holidays appear as gaps and clear after 200 bars. |
| `INSUFFICIENT_DATA` / `CANNOT_ASSESS` | fewer than 2700 stored M5 bars | `sync --all --days 90`. |
| `MARKET_CLOSED` | weekend / daily break | none, expected. |
| `CALENDAR_UNAVAILABLE` | news feed unreachable or older than 36 h | `python main.py calendar`; trading stays allowed under policy `warn`, blocked under `block` (`NEWS_UNAVAILABLE_POLICY`). |
| `MODEL_STALE_MODEL` warning | approved model older than 30 days or feature schema changed; the engine falls back to rules-only | retrain: `train --symbol X`, review, `model-approve`. |
| Sync reports `BLOCKED` | missing credentials / package (OANDA token, Twelve Data key, MT5 terminal) | set the key in `.env`; run `doctor --deep`. Nothing is faked meanwhile. |
| `checksum mismatch` loading a model | artifact modified or corrupt | `model-rollback --symbol X`, retrain. |
| Laptop was off for days | missed candles only | `python main.py signal --symbol X --auto-sync` (catch-up back-fills closed bars; the CLI prints the recovered range, gaps and freshness before analysing). Ticks and spread spikes during the outage are unrecoverable and not invented. |
| Ungraceful shutdown | SQLite WAL is crash-safe; jobs left `RUNNING` stay in the table | restart; re-run the job. Paper positions are idempotent so no duplicates are created. |
| Wi-Fi outage during monitor | sync returns `FAILED` for that cycle | the monitor keeps running; the next cycle retries and back-fills. |
| Dashboard says MODEL NOT READY | no approved model: rules-only baseline | expected until a model passes validation and you approve it. |

Backup/restore: stop the server, copy `data/forex.db` (+ `-wal`, `-shm` if present) and `models/`; restore by copying back. Cached Dukascopy day files in `data/cache` can be deleted and re-downloaded.
