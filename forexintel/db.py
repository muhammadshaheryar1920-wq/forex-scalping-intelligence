"""SQLAlchemy Core schema (SQLite by default, PostgreSQL-ready)."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Iterable

from sqlalchemy import (JSON, Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, event, select, text)
from sqlalchemy.engine import Engine

from .config import get_settings

metadata = MetaData()

instruments = Table(
    "instruments", metadata,
    Column("symbol", String(16), primary_key=True), Column("display", String(16)),
    Column("asset_class", String(16)), Column("quote_precision", Integer), Column("tick_size", Float),
    Column("pip_size", Float), Column("contract_size", Float), Column("provider_symbols", JSON),
    Column("active", Boolean, default=True),
)

candles = Table(
    "candles", metadata,
    Column("provider", String(16), primary_key=True), Column("symbol", String(16), primary_key=True),
    Column("timeframe", String(4), primary_key=True), Column("open_ts", Integer, primary_key=True),
    Column("close_ts", Integer),
    Column("open", Float), Column("high", Float), Column("low", Float), Column("close", Float),
    Column("bo", Float), Column("bh", Float), Column("bl", Float), Column("bc", Float),
    Column("ao", Float), Column("ah", Float), Column("al", Float), Column("ac", Float),
    Column("volume", Float), Column("n_src", Integer), Column("is_complete", Boolean),
    Column("has_bid_ask", Boolean), Column("source_hash", String(24)), Column("received_ts", Integer),
)
Index("ix_candles_lookup", candles.c.symbol, candles.c.timeframe, candles.c.provider, candles.c.open_ts)

quotes = Table(
    "quotes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String(16)), Column("symbol", String(16)),
    Column("source_ts", Integer), Column("received_ts", Integer),
    Column("bid", Float), Column("ask", Float), Column("spread", Float), Column("status", String(16)),
    UniqueConstraint("provider", "symbol", "source_ts", name="uq_quote_identity"),
)

sync_state = Table(
    "sync_state", metadata,
    Column("provider", String(16), primary_key=True), Column("symbol", String(16), primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("last_complete_open_ts", Integer), Column("last_sync_ts", Integer),
    Column("status", String(24)), Column("detail", JSON),
)

economic_events = Table(
    "economic_events", metadata,
    Column("event_id", String(40), primary_key=True), Column("currency", String(8)),
    Column("title", String(200)), Column("scheduled_ts", Integer), Column("impact", String(12)),
    Column("forecast", String(40)), Column("previous", String(40)), Column("actual", String(40)),
    Column("published_ts", Integer), Column("source", String(40)),
)

signals = Table(
    "signals", metadata,
    Column("id", String(40), primary_key=True), Column("created_ts", Integer), Column("asof_ts", Integer),
    Column("symbol", String(16)), Column("provider", String(32)), Column("feed_status", String(24)),
    Column("regime", String(16)), Column("bias", String(12)), Column("bias_prob", Float),
    Column("action", String(12)), Column("strength", String(12)), Column("strategy", String(32)),
    Column("entry", Float), Column("sl", Float), Column("tp1", Float), Column("tp2", Float), Column("tp3", Float),
    Column("prob", Float), Column("ev_r", Float), Column("expiry_ts", Integer),
    Column("blockers", JSON), Column("payload", JSON), Column("model_version", String(40)),
    Column("state", String(16)),
    UniqueConstraint("symbol", "asof_ts", "provider", name="uq_signal_bar"),
)

strategy_candidates = Table(
    "strategy_candidates", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("signal_id", String(40)),
    Column("symbol", String(16)), Column("asof_ts", Integer), Column("strategy", String(32)),
    Column("side", String(6)), Column("regime", String(16)), Column("plan", JSON),
    Column("status", String(12)), Column("reasons", JSON), Column("rank", Float),
)

models = Table(
    "models", metadata,
    Column("version", String(60), primary_key=True), Column("symbol", String(16)), Column("kind", String(20)),
    Column("algo", String(20)), Column("created_ts", Integer), Column("dataset_hash", String(40)),
    Column("train_end_ts", Integer), Column("feature_version", String(12)), Column("metrics", JSON),
    Column("status", String(16)), Column("artifact_path", Text), Column("checksum", String(64)),
    Column("rollback_to", String(60)),
)

backtest_runs = Table(
    "backtest_runs", metadata,
    Column("id", String(40), primary_key=True), Column("symbol", String(16)), Column("created_ts", Integer),
    Column("kind", String(24)), Column("params", JSON), Column("results", JSON),
)

strategy_stats = Table(
    "strategy_stats", metadata,
    Column("symbol", String(16), primary_key=True), Column("strategy", String(32), primary_key=True),
    Column("computed_ts", Integer), Column("n", Integer), Column("win_rate", Float), Column("wr_lo", Float),
    Column("wr_hi", Float), Column("ev_r", Float), Column("ev_lo", Float), Column("pf", Float),
    Column("max_dd_r", Float), Column("run_id", String(40)), Column("detail", JSON),
)

paper_positions = Table(
    "paper_positions", metadata,
    Column("id", String(40), primary_key=True), Column("signal_id", String(40)), Column("symbol", String(16)),
    Column("side", String(6)), Column("strategy", String(32)), Column("authorized", Boolean),
    Column("state", String(12)), Column("created_ts", Integer), Column("signal_asof_ts", Integer),
    Column("entry_ts", Integer), Column("entry_price", Float), Column("sl", Float), Column("tp1", Float),
    Column("tp2", Float), Column("tp3", Float), Column("units", Float), Column("risk_amount", Float),
    Column("exit_ts", Integer), Column("exit_price", Float), Column("exit_reason", String(16)),
    Column("net_r", Float), Column("pnl", Float), Column("fees", Float), Column("detail", JSON),
)

jobs = Table(
    "jobs", metadata,
    Column("id", String(40), primary_key=True), Column("type", String(24)), Column("status", String(12)),
    Column("created_ts", Integer), Column("started_ts", Integer), Column("finished_ts", Integer),
    Column("params", JSON), Column("log", Text), Column("result", JSON),
)

audits = Table(
    "audits", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Integer),
    Column("actor", String(32)), Column("event", String(48)), Column("detail", JSON),
)

_engine: Engine | None = None
_engine_url: str | None = None


def get_engine() -> Engine:
    global _engine, _engine_url
    url = get_settings().database_url
    if _engine is None or _engine_url != url:
        kw = {}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, future=True, **kw)
        _engine_url = url
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragma(dbapi_con, _):  # noqa: ANN001
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
    return _engine


def reset_engine() -> None:
    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine, _engine_url = None, None


def _migrate() -> None:
    """Add columns introduced after a database file was first created (lightweight, additive only)."""
    from sqlalchemy import inspect
    eng = get_engine()
    insp = inspect(eng)
    with eng.begin() as conn:
        for t in metadata.sorted_tables:
            if not insp.has_table(t.name):
                continue
            have = {c["name"] for c in insp.get_columns(t.name)}
            for col in t.columns:
                if col.name not in have:
                    ctype = col.type.compile(dialect=eng.dialect)
                    conn.execute(text(f'ALTER TABLE {t.name} ADD COLUMN {col.name} {ctype}'))


def init_db() -> None:
    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata.create_all(get_engine())
    except Exception as e:  # concurrent first-run from another process: tables already created
        if "already exists" not in str(e):
            raise
    _migrate()


@contextmanager
def begin():
    with get_engine().begin() as conn:
        yield conn


def upsert(conn, table: Table, rows: Iterable[dict], keys: list[str], update: bool = True) -> int:
    """Dialect-aware insert-or-update. Returns number of rows submitted."""
    rows = list(rows)
    if not rows:
        return 0
    dialect = conn.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = insert(table).values(chunk)
        if update:
            cols = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in keys}
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        conn.execute(stmt)
    return len(rows)


def audit(event_name: str, detail: dict | None = None, actor: str = "system") -> None:
    with begin() as c:
        c.execute(audits.insert().values(ts=int(time.time()), actor=actor, event=event_name, detail=detail or {}))


def seed_instruments() -> None:
    from .config import INSTRUMENTS
    with begin() as c:
        upsert(c, instruments, [dict(
            symbol=i.symbol, display=i.display, asset_class=i.asset_class, quote_precision=i.quote_precision,
            tick_size=i.tick_size, pip_size=i.pip_size, contract_size=i.contract_size,
            provider_symbols=i.provider_symbols, active=True) for i in INSTRUMENTS.values()], ["symbol"])
