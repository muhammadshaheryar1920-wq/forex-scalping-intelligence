"""Training protocol: purge/embargo, no auto-activation, calibration, registry approval/rollback/integrity."""
import time

import numpy as np
import pytest

from forexintel import db
from forexintel.config import get_settings
from forexintel.research import registry, train
from forexintel.research.dataset import build_events, model_matrix, orient, prepare
from forexintel.research.models import Bundle, Platt, make_model, prob_metrics, reliability, select_threshold
from tests.conftest import seed


def test_split_purges_overlapping_label_windows():
    m5 = seed("EURUSD", 90)
    F = prepare(m5)
    ev, _ = build_events(F, "EURUSD")
    H = get_settings().horizon_bars
    masks = train.split_events(ev, H)
    i = ev["i"].to_numpy()
    order = ["train", "cal", "th", "test"]
    for a, b in zip(order[:-1], order[1:]):
        assert masks[a].any() and masks[b].any()
        # label window of the last earlier-segment event ends before the first later-segment decision bar
        assert i[masks[a]].max() + H + 1 < i[masks[b]].min()
    # chronological: segments do not interleave
    assert i[masks["train"]].max() < i[masks["cal"]].min() < i[masks["th"]].min() < i[masks["test"]].min()


def test_orient_mirrors_short_trades():
    m5 = seed("EURUSD", 45)
    F = prepare(m5)
    ev, _ = build_events(F, "EURUSD")
    long_row = ev[ev["side"] == 1].iloc[[0]].copy()
    short_row = long_row.copy()
    short_row["side"] = -1
    xl, xs = model_matrix(long_row).iloc[0], model_matrix(short_row).iloc[0]
    assert xs["trend"] == -xl["trend"] or (np.isnan(xs["trend"]) and np.isnan(xl["trend"]))
    assert xs["rsi"] == pytest.approx(100 - xl["rsi"])
    assert xs["dist_sh"] == pytest.approx(-xl["dist_sl"], nan_ok=True)
    assert xs["fvg_bull_d"] == pytest.approx(xl["fvg_bear_d"], nan_ok=True)


def test_platt_calibration_fixes_overconfidence():
    rng = np.random.default_rng(0)
    p_true = rng.uniform(0.3, 0.7, 4000)
    y = (rng.uniform(size=4000) < p_true).astype(int)
    p_over = np.clip(0.5 + (p_true - 0.5) * 3.0, 0.01, 0.99)          # model far too confident
    cal = Platt().fit(p_over[:2000], y[:2000])
    assert prob_metrics(y[2000:], cal.predict(p_over[2000:]))["brier"] < prob_metrics(y[2000:], p_over[2000:])["brier"]
    bins = reliability(y[2000:], cal.predict(p_over[2000:]))
    assert all(abs(b["p_mean"] - b["obs"]) < 0.08 for b in bins if b["n"] > 100)


def test_threshold_selection_requires_positive_expectancy_and_min_trades():
    p = np.linspace(0.3, 0.8, 400)
    r_neg = np.full(400, -0.2)
    assert select_threshold(p, r_neg, 20)[0] is None                  # abstain when nothing is profitable
    r = np.where(p > 0.6, 0.5, -0.3)
    thr, info = select_threshold(p, r, 20)
    assert thr is not None and thr >= 0.55 and info["best_ev"] > 0
    assert select_threshold(p[:30], r[:30], 100)[0] is None           # not enough trades to trust


def test_training_on_random_data_never_activates_a_model_and_reports_everything():
    seed("EURUSD", 100)
    rep = train.train_trade_model("EURUSD", algos=("logistic", "lightgbm"))
    assert rep["status"] in ("REJECTED", "CANDIDATE", "NOT_TRAINED")
    assert registry.active_model("EURUSD", "trade") is None           # approval is always a separate human step
    if rep["status"] != "NOT_TRAINED":
        for k in ("split_sizes", "baseline_test", "algos", "dataset_hash", "version", "split_bounds_ts"):
            assert k in rep
        assert rep["algos"]["logistic"]["val_metrics"]["n"] > 0
        rows = registry.list_models("EURUSD", "trade")
        assert rows and rows[0]["status"] == rep["status"] and rows[0]["checksum"]
        if rep["status"] == "REJECTED":
            with pytest.raises(ValueError):
                registry.approve(rep["version"])


def _fake_bundle(algo="logistic"):
    import pandas as pd
    X = pd.DataFrame({"a": np.arange(20.0), "b": np.arange(20.0)[::-1]})
    m = make_model(algo).fit(X, np.array([0, 1] * 10))
    cal = Platt().fit(m.predict_proba(X)[:, 1], np.array([0, 1] * 10))
    return Bundle(algo, m, cal, ["a", "b"], 0.55, 0.9, -1.0)


def test_registry_approve_rollback_and_integrity(tmp_path):
    v1 = registry.save_bundle(_fake_bundle(), "EURUSD", "trade", "h1", int(time.time()), {"k": 1}, "CANDIDATE")
    assert registry.active_model("EURUSD") is None
    registry.approve(v1)
    assert registry.active_model("EURUSD")["version"] == v1
    time.sleep(1.1)                                                   # versions are timestamped to the second
    v2 = registry.save_bundle(_fake_bundle(), "EURUSD", "trade", "h2", int(time.time()), {"k": 2}, "CANDIDATE")
    out = registry.approve(v2)
    assert out["previous"] == v1 and registry.active_model("EURUSD")["version"] == v2
    rb = registry.rollback("EURUSD")
    assert rb["now_active"] == v1 and registry.active_model("EURUSD")["version"] == v1
    rb2 = registry.rollback("EURUSD")
    assert rb2["now_active"] == "rules-only baseline" and registry.active_model("EURUSD") is None
    row = registry.list_models("EURUSD")[0]
    path = row["artifact_path"]
    open(path, "ab").write(b"tamper")
    with pytest.raises(RuntimeError, match="checksum"):
        registry.load_bundle(row)


def test_model_health_flags_stale_and_schema_mismatch():
    row = {"version": "v", "algo": "x", "status": "APPROVED", "created_ts": time.time() - 40 * 86400, "feature_version": "f1"}
    assert registry.health(row)["state"] == "STALE" and "STALE_MODEL" in registry.health(row)["flags"]
    row2 = {**row, "created_ts": time.time(), "feature_version": "f0"}
    assert "FEATURE_SCHEMA_MISMATCH" in registry.health(row2)["flags"]
    assert registry.health(None)["state"] == "NO_MODEL"
