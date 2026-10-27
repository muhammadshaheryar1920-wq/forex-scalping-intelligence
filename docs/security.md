# Security notes (local deployment)

- **Bind address**: the server defaults to `127.0.0.1`. `main.py serve --host 0.0.0.0` is refused unless `API_TOKEN` is set. Do not expose the port to the Internet.
- **Secrets**: only in `.env` (git-ignored). `/api/v1/settings` shows booleans, never values; tests assert that. Keys are never sent to the browser or written to logs.
- **API token**: if `API_TOKEN` is set every endpoint except `/health` requires it (Bearer or `X-API-Key`; SSE also `?token=`). Comparison is exact; there is no user database.
- **CORS** is limited to localhost origins used by the dashboard / a future dev server on port 3000.
- **Browser output**: the dashboard escapes all API-provided text before inserting it into the DOM (news titles, reasons, job logs).
- **Model artifacts** are joblib pickles. They are checksum-verified against the registry before loading, but never load artifacts from untrusted sources: unpickling executes code.
- **Downloaded data** (Dukascopy day files) is parsed with fixed-size struct decoding after LZMA decompression; malformed files raise and are retried, never executed.
- **No order code**: there is no broker order submission anywhere in the repository.
- **Third-party scripts**: the dashboard loads `lightweight-charts@4.1.3` from unpkg (pinned version). Vendor it into `static/` for an air-gapped setup.
- **Backups**: copy `data/forex.db` and `models/` (SQLite is a single file; stop the server first or use `sqlite3 .backup`).
