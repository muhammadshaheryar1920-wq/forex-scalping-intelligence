import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forexintel import config, db  # noqa: E402
from forexintel.data import store  # noqa: E402

TEST_PROVIDER = "testfeed"


@pytest.fixture(autouse=True)
def env(tmp_path):
    """Isolated DB + model dir per test; no network providers configured."""
    base = config.Settings.from_env()
    s = replace(base, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", models_dir=tmp_path / "models",
                data_dir=tmp_path / "data", history_provider=TEST_PROVIDER, live_provider="none", api_token="",
                news_unavailable_policy="warn")
    config.set_settings(s)
    db.reset_engine()
    store._series_cache.clear()
    from forexintel.strategy import engine
    engine._cache.clear()
    db.init_db()
    db.seed_instruments()
    yield s
    db.reset_engine()
    config.set_settings(None)


def seed(symbol="EURUSD", days=70, provider=TEST_PROVIDER):
    """Store deterministic SYNTHETIC bars under a fake provider name (tests only)."""
    from datetime import datetime, timedelta, timezone
    from forexintel.providers.demo import DemoProvider
    end = datetime.now(timezone.utc)
    f = DemoProvider().fetch_candles(symbol, "M5", end - timedelta(days=days), end)
    store.save_candles(f, provider, symbol)
    store._series_cache.clear()
    return f[f["is_complete"]]
