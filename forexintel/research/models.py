"""Model zoo (logistic baseline, LightGBM, XGBoost), Platt calibration and the fit/select pipeline."""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)

ALGOS = ("logistic", "lightgbm", "xgboost")
THRESHOLD_GRID = np.round(np.arange(0.35, 0.81, 0.025), 3)


def make_model(algo: str, seed: int = 7):
    if algo == "logistic":
        return Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler()),
                         ("lr", LogisticRegression(C=0.05, max_iter=1000))])
    if algo == "lightgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=5, min_child_samples=40,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, random_state=seed,
                              verbose=-1, n_jobs=2)
    if algo == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=250, max_depth=3, learning_rate=0.03, subsample=0.8, colsample_bytree=0.7,
                             reg_lambda=5.0, min_child_weight=5, eval_metric="logloss", tree_method="hist",
                             random_state=seed, n_jobs=2, verbosity=0)
    raise ValueError(f"unknown algo {algo}")


class Platt:
    """Sigmoid calibration on the logit of raw probabilities."""

    def __init__(self):
        self.lr = LogisticRegression(C=1e6, max_iter=1000)
        self.identity = False

    @staticmethod
    def _z(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, p, y):
        y = np.asarray(y)
        if len(np.unique(y)) < 2 or len(y) < 30:
            self.identity = True
        else:
            self.lr.fit(self._z(p), y)
        return self

    def predict(self, p):
        return np.asarray(p, dtype=float) if self.identity else self.lr.predict_proba(self._z(p))[:, 1]


def prob_metrics(y, p) -> dict:
    y, p = np.asarray(y), np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    out = {"n": int(len(y)), "base_rate": float(y.mean()) if len(y) else None}
    if len(y) and len(np.unique(y)) > 1:
        out.update(brier=float(brier_score_loss(y, p)), logloss=float(log_loss(y, p, labels=[0, 1])),
                   auc=float(roc_auc_score(y, p)))
    return out


def reliability(y, p, bins: int = 5) -> list[dict]:
    y, p = np.asarray(y), np.asarray(p)
    if len(y) == 0:
        return []
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p <= b) if b == edges[-1] else (p >= a) & (p < b)
        if m.sum():
            out.append({"p_mean": float(p[m].mean()), "obs": float(y[m].mean()), "n": int(m.sum())})
    return out


@dataclass
class Bundle:
    algo: str
    model: object
    calibrator: Platt
    cols: list
    threshold: float | None
    rwin: float
    rnon: float
    meta: dict = field(default_factory=dict)

    def proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(X[self.cols])[:, 1]
        return self.calibrator.predict(raw)

    def ev_hat(self, p) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        return p * self.rwin + (1 - p) * self.rnon


def select_threshold(p, net_r, min_n: int) -> tuple[float | None, dict]:
    best, info = None, {"best_ev": None, "n_sel": 0}
    for t in THRESHOLD_GRID:
        m = p >= t
        if m.sum() < min_n:
            continue
        ev = float(np.mean(net_r[m]))
        if ev > 0 and (info["best_ev"] is None or ev >= info["best_ev"]):
            best, info = float(t), {"best_ev": ev, "n_sel": int(m.sum())}
    return best, info


def fit_select(Xtr, ytr, rtr, Xcal, ycal, Xth, rth, yth, algo: str, cols: list, seed: int = 7) -> Bundle:
    """Fit on train, calibrate on cal, choose the trading threshold on a third chronological slice."""
    model = make_model(algo, seed)
    model.fit(Xtr[cols], ytr)
    cal = Platt().fit(model.predict_proba(Xcal[cols])[:, 1], ycal)
    p_th = cal.predict(model.predict_proba(Xth[cols])[:, 1])
    thr, info = select_threshold(p_th, np.asarray(rth), max(15, int(0.08 * len(Xth))))
    ytr, rtr = np.asarray(ytr), np.asarray(rtr)
    rwin = float(rtr[ytr == 1].mean()) if (ytr == 1).any() else 1.0
    rnon = float(rtr[ytr == 0].mean()) if (ytr == 0).any() else -1.0
    return Bundle(algo, model, cal, cols, thr, rwin, rnon, {"threshold_selection": info})
