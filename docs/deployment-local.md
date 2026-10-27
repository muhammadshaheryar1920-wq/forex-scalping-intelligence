# Local deployment and cloud migration notes

## Local (this repository)

1. `python -m venv .venv`, activate, `pip install -r requirements.txt`.
2. Optional `.env` from `.env.example` (OANDA practice token for live gold, `API_TOKEN` if you will ever bind beyond loopback).
3. `python main.py doctor` -> `sync --all --days 90` (or deeper, e.g. 365) -> `backtest --all --walk-forward` -> `train --symbol X` -> `dashboard`.
4. Always-on use while the laptop is on: `python main.py monitor --all --on-candle-close`, or Start monitor on the dashboard (the server must stay running). Nothing runs while the machine is off or asleep; on wake the next sync back-fills closed candles.
5. Windows Task Scheduler can start `monitor` at logon; it cannot run while the machine is powered off.

Hardware: no GPU, models are small (logistic, LightGBM, XGBoost). First 90-day sync of three symbols takes a few minutes (downloads are cached); a 1-year sync took about 20 minutes in testing because the Dukascopy server throttles.

## PostgreSQL (optional)

`pip install "psycopg[binary]"`, create a database, set `DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/forex`, run `python main.py doctor`. Tables are created automatically; the upsert helper uses the PostgreSQL dialect.

## Moving to a cloud edition later

- Reuse `forexintel/` unchanged. Run `main.py serve` (API) and `main.py monitor` (worker) as separate processes/services.
- Managed PostgreSQL via `DATABASE_URL`; model artifacts need a persistent volume or object storage because container disks are ephemeral (`Settings.models_dir`).
- Only one worker may emit signals: the unique constraint on `signals (symbol, asof_ts, provider)` and idempotent paper position ids already make double starts harmless; add a database advisory lock for strict single-leader behaviour.
- Put the dashboard behind HTTPS and set `API_TOKEN`; `static/` can be served by any host or replaced by a Next.js app calling the same `/api/v1`.
- Re-verify provider terms (data redistribution, commercial use) before hosting anything publicly.
