# Risk policy (paper trading only)

| Control | Default | Where |
|---|---|---|
| Risk per paper trade | 0.5% of simulated equity (`RISK_PER_TRADE_PCT`), equity 10 000 (`PAPER_EQUITY`) | `paper/journal.py`, `strategy/plan.position_size` |
| Daily circuit breaker | new *authorized* paper trades stop after realised loss >= 2% of equity that UTC day (`DAILY_LOSS_LIMIT_PCT`); shadow trades do not count | `journal.circuit_breaker` |
| Exposure | one active authorized position per symbol; at most two same-direction USD exposures (all three pairs quote USD) | `journal.exposure_blockers` |
| Spread / cost | block when spread > 25% of ATR(14) M5; commission and slippage included in every R | `strategy/risk.py`, `plan.py` |
| News | blackout 30 min before / 15 min after high-impact events for XAU: USD; EURUSD: EUR+USD; GBPUSD: GBP+USD. `NEWS_UNAVAILABLE_POLICY=warn\|block` when the calendar cannot be fetched (default `warn`, labelled in the output) | `features/news.py` |
| Market closed / rollover | no signal when the last bar is not current for an open market, or the market is closed (Fri 17:00 to Sun 17:00 New York, gold daily 17:00-18:00 break) | `data/quality.py`, `data/calendar.py` |
| Feed failure | `STALE / DEGRADED / INSUFFICIENT_DATA` block actionable output and are shown in the UI; expired quotes are never used silently | `quality.assess` |
| Forbidden | martingale, grid recovery, averaging down, unattended real-money trading: **not implemented** | - |

Position sizing (USD-quoted instruments): `units = risk_amount / stop_distance`; lots = `units / contract_size` (EURUSD/GBPUSD 100 000, XAUUSD 100 oz).

Everything here is configurable in `forexintel/config.py` / `.env`. None of it is investment advice; the numbers describe a *simulation*.
