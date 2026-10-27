# Algorithm rules (measurable definitions)

All rules are deterministic and **causal**: a value at bar `t` uses only bars `<= t`. Tests
(`tests/test_indicators_structure.py`, `tests/test_temporal.py`) truncate the data and assert that features at `t` do not change.
Decision time = close of the M5 bar (`bar open + 5 min`). M15/H1 context enters only once those bars have closed.

## Indicators (features, never gates)

EMA 20/50/200 (distance in ATR units and 5-bar slope), RSI(14) Wilder, ATR(14) Wilder, ADX(14), MACD histogram / ATR, realised volatility (20),
ATR percentile rank vs the *preceding* 500 bars (adaptive, excludes the current bar), candle body/wick fractions, returns over 1/3/6 bars in ATR.

## Market structure (M5 k=3, M15 k=3, H1 k=2)

- **Swing high at i**: `high[i] > max(high[i-k..i-1])` and `high[i] >= max(high[i+1..i+k])`. It is **confirmed at bar i+k** and unknown before. Swing lows mirror this.
- **Trend**: +1 when the latest confirmed swing high and swing low are both higher than the previous ones (HH+HL), -1 for LH+LL, otherwise unchanged. A CHoCH flips it immediately.
- **BOS**: a bar *closes* beyond the last confirmed swing level in the direction of the current trend (or the first break when trend is unknown). Fires once per level.
- **CHoCH**: a bar closes beyond the last confirmed swing level *against* the current trend (close below the last higher low in an up-trend, and vice versa).
- **Liquidity sweep**: `high > swing_high + 0.05*ATR` but `close < swing_high` (wick takes the level, bar closes back inside); the level must still be unbroken. Mirror for lows.
- **Fair value gap**: bullish if `low[t] > high[t-2]` and the gap >= 0.2 ATR (zone = [high[t-2], low[t]]), bearish mirrored. Active until a close passes through the far edge or 288 bars pass. It is a *price-gap measurement*, not an order-book imbalance.
- **Order block**: when a BOS/CHoCH bar closes with displacement >= 1.2 ATR from the candle, the last opposite-colour candle within the previous 10 bars defines the zone (its full range). Invalidated by a close beyond the far side.
- **Sessions**: Asia 09:00-18:00 Tokyo, London 08:00-17:00 London, New York 08:00-17:00 New York, Mon-Fri, via IANA zones (DST-safe). Asia running high/low are causal.

## Regime (`strategy/regime.py`)

- `TREND_UP/DOWN` if (M15 ADX >= 22 or H1 ADX >= 25) and `sign(2*H1_trend + M15_trend)` is non-zero; otherwise `RANGE`.
- Volatility: `HIGH` if ATR percentile > 0.90, `QUIET` if < 0.15, else `NORMAL`. Display regime is `HIGH_VOL`/`QUIET` when those apply.

## Strategies (independent, each logs every candidate)

| Strategy | Eligibility | Trigger (closed M5 bar) | SL anchor |
|---|---|---|---|
| `TREND_CONTINUATION` | base regime trending, vol not QUIET | price tagged EMA20 within the last 10 bars (pullback), then closes above the prior high and EMA20 on a bullish bar, not extended (EMA50 distance < 3 ATR). Short mirrored. | lowest low of last 8 bars |
| `BREAKOUT_RETEST` | vol not QUIET | BOS 2-20 bars ago, no close back through the level since the break, current bar retests within 0.25 ATR and closes back beyond it on a rejection candle; first retest per break only | min(low, broken level) |
| `LIQUIDITY_SWEEP_REVERSAL` | not against a trending regime | sweep 1-3 bars ago, then a close beyond the prior bar's extreme in the reversal direction; first per sweep | sweep wick extreme |
| `RANGE_MEAN_REVERSION` | base regime RANGE, vol NORMAL | 48-bar range 2.5-9 ATR wide; close in the outer 20% with rejection wick >= 30% and bullish/bearish close | range boundary |

## Trade plan (`strategy/plan.py`)

- Entry reference: long at **ask close**, short at **bid close**; executes at the next bar open (see simulate). Zone = reference to +/-0.25 ATR.
- SL = anchor -/+ 0.1 ATR (or 1.2 ATR if no valid anchor), at least max(instrument minimum, 3x spread, 0.8 ATR); rejected as `SL_TOO_WIDE` beyond 2.5 ATR (never silently tightened).
- TP1 = 1.0 R, TP2 = 1.8 R, TP3 = 2.8 R. TP2/TP3 are **suppressed** when a structural level (swing, M15/H1 swing, Asia range) blocks the path closer than 1.3 R / 2.0 R, and capped at that level when it is further.
- `expected_move_pct = 100 * (target - reference) / reference` (negative for shorts).
- Net cost in R = (commission + 2 x slippage) / risk; spread is paid inside the fills because exits happen on the opposite side.

## Hard vetoes (the only ones)

Data not HEALTHY (no data, insufficient, stale, degraded, market closed), demo data, news blackout (30 min before / 15 min after high-impact events for the pair's currencies,
policy for unavailable calendar), daily paper circuit breaker, spread > 25% of ATR, invalid stop, open position on the symbol, more than two same-direction USD exposures,
and **no validated edge / non-positive expected value**. A single RSI/MACD/SMC mismatch is never a veto.
