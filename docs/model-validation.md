# Model validation protocol and current evidence

## Labels

- **Trade label** (`y`): for a candidate created at the close of M5 bar `t`, entry at bar `t+1` open on the correct side of the spread plus slippage; `y = 1` if TP1 (1.0 R, price-side aware) is reached **before** the stop within 12 M5 bars, otherwise 0 (stop first, timeout, and same-bar ties all count as 0). Realised net R (incl. commission, gap-through-stop, timeout exit) is stored next to it. No-fill (adverse gap > 0.25 ATR) rows are dropped and counted.
- **Direction label**: `close[t+h] > close[t]` for h in 3/6/12 bars, skipping moves smaller than 0.15 ATR. Separate from, and never conflated with, trade win rate.

## Protocol (`research/train.py`, `research/backtest.py`)

1. Events = every trigger of every strategy (including ones later rejected) on the full stored history; features are point-in-time.
2. Chronological split by event: train 55% / calibration 15% / threshold-and-model-selection 15% / **untouched test 15%**. Each earlier segment drops events whose label window (`horizon + 2` bars) plus a 6-bar embargo would reach the next segment (purge + embargo; tested).
3. Candidates: logistic regression, LightGBM, XGBoost (small, regularised). Fit on train only; **Platt calibration on the calibration slice**; trading threshold chosen on the threshold slice as the cut-off with the best net expectancy, requiring positive expectancy and enough trades. If none exists the model *abstains* (threshold `None`).
4. Model choice uses validation only. The chosen, frozen model is evaluated **once** on the test slice and compared with the rules-only baseline (positions are non-overlapping). `CANDIDATE` requires >= 30 selected trades, positive net EV and EV above the baseline; "significant" additionally needs the 95% CI lower bound above 0. Everything else is `REJECTED` and cannot be approved.
5. Reported: calibration bins, Brier, log loss, AUC, feature-group ablation (validation), per-strategy results, win rate with Wilson interval, EV with interval, profit factor, drawdown.
6. Walk-forward (`backtest --walk-forward`): expanding window, 5 test blocks after the first 50%; each fold re-fits, re-calibrates and re-selects its threshold using only data before the fold (purged). Only out-of-sample selections are aggregated.
7. Direction models: 60/20/20 chronological, purge `h + 6` bars, calibrated on validation, balanced accuracy on test with an **overlap-aware** interval (`n_eff = n / h`) because forward labels overlap.
8. Models are saved as checksum-verified joblib artifacts with dataset hash, feature version and split bounds; **nothing is activated without `model-approve`**; `model-rollback` restores the previous model or the rules-only baseline; models older than 30 days or with a different feature schema are ignored with a warning.

Costs in every simulated trade: real bid/ask (Dukascopy) for the fill side, slippage = 25% of the spread per fill, round-turn commission (0.7 pip for FX, $0.07 per oz for gold), stop-first on ambiguous bars. News filtering is **not** applied in backtests (no point-in-time calendar history), which flatters results if anything.

## Evidence on real data (12 months, Oct 2025 - Oct 2026, ~70-75k M5 bars per symbol)

| | XAU/USD | EUR/USD | GBP/USD |
|---|---|---|---|
| Candidate trades (all strategies) | 5 604 | 5 350 | 5 302 |
| Pooled rules-only, 1 position at a time: trades / win rate | 3 252 / 36.0% | 3 079 / 32.8% | 3 055 / 32.3% |
| Net expectancy per trade | **-0.15 R** | **-0.29 R** | **-0.29 R** |
| Profit factor | 0.71 | 0.51 | 0.51 |
| Every strategy individually | all negative | all negative | all negative |
| Walk-forward ML selections (OOS) | none chosen | none chosen | logistic: 57 trades, -0.33 R |
| Trade-model test AUC (frozen) | 0.51 | 0.57 | 0.63 |
| Trade model status | REJECTED | REJECTED | REJECTED |
| Direction balanced accuracy h=3 / 6 / 12 | 0.506 / 0.500 / 0.501 | 0.512 / 0.511 / 0.499 | 0.517 / 0.524 / 0.522 |
| Direction models passing the overlap-aware test | none | none | h=3, h=6 (weak, see below) |

Diagnostics on 3 months of EUR/USD: the same triggers with **zero** spread, slippage and commission have an expectancy of about +0.01 R, while the real cost averages about 0.18 R of the (about 1.9 ATR) stop. In other words there is no gross edge to pay for the costs. A small grid (3 symbols x 3 TP multiples x 2 horizons x each strategy and pooled) found **no** configuration with positive expectancy in both halves of the data.

### Interpretation

- Classical price-action/SMC triggers on M5 do not beat realistic trading costs on this data. The bot therefore returns `WAIT` with `NO_VALIDATED_EDGE` and never fabricates a plan or a probability.
- GBP/USD direction models show a small, statistically borderline skill (balanced accuracy 52%). They are registered as `CANDIDATE` only. A 52% directional edge is far too small to pay M5 trading costs, and with 9 direction tests the chance of a few lucky passes is not negligible. Treat as a research lead, not a signal source.
- The GBP/USD trade-model AUC (0.63) shows the model can rank which setups are more likely to reach TP1, but no probability cut-off produced positive net expectancy.
- The **70-80% win-rate target is not met and not promised.** Observed per-strategy win rates are 27-43% for a 1R target after costs. A high win rate needs a target much smaller than the stop; that trades a high win rate for a negative expectancy, which the system refuses to present as an edge.

## How to keep researching (honestly)

1. More history and regimes: `python main.py sync --all --days 730`, then `backtest` / `train` again.
2. Reduce cost drag: wider structure targets and holds (higher-timeframe scalps), trade only low-spread sessions, use a lower-cost real broker account (OANDA/Dukascopy-style spreads) and edit `commission_rt` in `config.py`.
3. New information, not more of the same price features: cross-asset series (USD index, yields, equity indices), calendar surprise data, order-flow data if a licensed source becomes available.
4. Add a strategy as one new function in `strategy/strategies.py`, then judge it by out-of-sample net expectancy (`backtest`), never by win rate.
5. Forward evidence: leave `monitor` running; shadow paper trades record what the unvalidated candidates would have done, with the same fill rules.
