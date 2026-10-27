"""Background jobs with real IDs and persisted logs (sync / train / backtest)."""
from __future__ import annotations

import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from .. import db
from ..research.metrics import clean

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")


def _log(job_id: str, line: str) -> None:
    with db.begin() as c:
        cur = c.execute(select(db.jobs.c.log).where(db.jobs.c.id == job_id)).scalar() or ""
        c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(log=cur + f"[{time.strftime('%H:%M:%S', time.gmtime())}Z] {line}\n"))


def submit(job_type: str, params: dict, fn) -> str:
    job_id = f"{job_type}-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:4]}"
    with db.begin() as c:
        c.execute(db.jobs.insert().values(id=job_id, type=job_type, status="QUEUED", created_ts=int(time.time()), params=params, log=""))

    def run():
        with db.begin() as c:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="RUNNING", started_ts=int(time.time())))
        _log(job_id, f"started {job_type} {params}")
        try:
            result = fn(lambda m: _log(job_id, m))
            with db.begin() as c:
                c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="DONE", finished_ts=int(time.time()), result=clean(result)))
            _log(job_id, "finished OK")
        except Exception as e:  # noqa: BLE001
            with db.begin() as c:
                c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="FAILED", finished_ts=int(time.time()),
                                                                                 result={"error": f"{type(e).__name__}: {e}"}))
            _log(job_id, "FAILED: " + traceback.format_exc(limit=3))

    _pool.submit(run)
    db.audit("job_submitted", {"id": job_id, "type": job_type, "params": params})
    return job_id


def get(job_id: str) -> dict | None:
    with db.get_engine().connect() as c:
        r = c.execute(select(db.jobs).where(db.jobs.c.id == job_id)).first()
    return None if r is None else dict(r._mapping)


def list_jobs(limit: int = 30) -> list[dict]:
    with db.get_engine().connect() as c:
        return [{k: v for k, v in dict(r._mapping).items() if k != "log"}
                for r in c.execute(select(db.jobs).order_by(db.jobs.c.created_ts.desc()).limit(limit))]
