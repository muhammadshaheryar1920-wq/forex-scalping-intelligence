"""Versioned model artifacts: save, register, approve (human), roll back, health/staleness checks."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import select, update

from .. import db
from ..config import get_settings
from ..features.builder import FEATURE_VERSION
from .metrics import clean
from .models import Bundle

STALE_AFTER_DAYS = 30


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_bundle(bundle: Bundle, symbol: str, kind: str, dataset_hash: str, train_end_ts: int, metrics: dict,
                status: str, extra: dict | None = None) -> str:
    db.init_db()
    version = f"{symbol}-{kind}-{bundle.algo}-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    d = get_settings().models_dir / symbol
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{version}.joblib"
    bundle.meta.update(extra or {})
    bundle.meta.update(version=version, symbol=symbol, kind=kind, feature_version=FEATURE_VERSION)
    joblib.dump(bundle, path)
    with db.begin() as c:
        db.upsert(c, db.models, [dict(
            version=version, symbol=symbol, kind=kind, algo=bundle.algo, created_ts=int(time.time()),
            dataset_hash=dataset_hash, train_end_ts=int(train_end_ts), feature_version=FEATURE_VERSION,
            metrics=clean(metrics), status=status, artifact_path=str(path), checksum=_sha(path), rollback_to=None)],
            ["version"])
    db.audit("model_saved", {"version": version, "status": status})
    return version


def list_models(symbol: str | None = None, kind: str | None = None) -> list[dict]:
    db.init_db()
    q = select(db.models).order_by(db.models.c.created_ts.desc())
    if symbol:
        q = q.where(db.models.c.symbol == symbol)
    if kind:
        q = q.where(db.models.c.kind == kind)
    with db.get_engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def active_model(symbol: str, kind: str = "trade") -> dict | None:
    for m in list_models(symbol, kind):
        if m["status"] == "APPROVED":
            return m
    return None


def load_bundle(version_or_row) -> Bundle:
    row = version_or_row if isinstance(version_or_row, dict) else next(
        (m for m in list_models() if m["version"] == version_or_row), None)
    if row is None:
        raise KeyError(f"model {version_or_row} not found")
    path = Path(row["artifact_path"])
    if _sha(path) != row["checksum"]:
        raise RuntimeError(f"checksum mismatch for {row['version']}: artifact modified or corrupt")
    return joblib.load(path)


def approve(version: str, actor: str = "owner") -> dict:
    row = next((m for m in list_models() if m["version"] == version), None)
    if row is None:
        raise KeyError(version)
    if row["status"] == "REJECTED":
        raise ValueError(f"{version} was REJECTED by validation; refusing to approve it")
    prev = active_model(row["symbol"], row["kind"])
    with db.begin() as c:
        if prev and prev["version"] != version:
            c.execute(update(db.models).where(db.models.c.version == prev["version"]).values(status="ARCHIVED"))
        c.execute(update(db.models).where(db.models.c.version == version).values(
            status="APPROVED", rollback_to=prev["version"] if prev and prev["version"] != version else None))
    db.audit("model_approved", {"version": version, "previous": prev["version"] if prev else None}, actor=actor)
    return {"approved": version, "previous": prev["version"] if prev else None}


def rollback(symbol: str, kind: str = "trade", actor: str = "owner") -> dict:
    cur = active_model(symbol, kind)
    if cur is None:
        return {"rolled_back": None, "note": "no approved model: already on the rules-only baseline"}
    target = cur.get("rollback_to")
    with db.begin() as c:
        c.execute(update(db.models).where(db.models.c.version == cur["version"]).values(status="ARCHIVED"))
        if target:
            c.execute(update(db.models).where(db.models.c.version == target).values(status="APPROVED"))
    db.audit("model_rollback", {"from": cur["version"], "to": target}, actor=actor)
    return {"rolled_back": cur["version"], "now_active": target or "rules-only baseline"}


def health(row: dict | None, data_last_ts=None) -> dict:
    if row is None:
        return {"state": "NO_MODEL", "detail": "rules-only baseline in use"}
    age_days = (time.time() - row["created_ts"]) / 86400
    flags = []
    if row["feature_version"] != FEATURE_VERSION:
        flags.append("FEATURE_SCHEMA_MISMATCH")
    if age_days > STALE_AFTER_DAYS:
        flags.append("STALE_MODEL")
    return {"state": "STALE" if flags else "OK", "flags": flags, "age_days": round(age_days, 1),
            "version": row["version"], "algo": row["algo"], "status": row["status"]}
