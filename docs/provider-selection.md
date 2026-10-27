# Provider capability matrix

"Verified" means this project downloaded and decoded real data from the service (26-27 Oct 2026). Everything else is
taken from official documentation and must be proven with your own credentials via `python main.py doctor --deep`.

| Provider | Cost | XAU/USD | EUR/USD | GBP/USD | Bid/ask | History | Live | Status |
|---|---|---|---|---|---|---|---|---|
| **Dukascopy** public datafeed | free | yes | yes | yes | **real** (BID and ASK files) | years of M1; M5 built here | ~1 day lag | **verified** |
| **Yahoo** chart API | free | **no** (`XAUUSD=X` 404, `GC=F` is futures) | yes | yes | no (mid only, spread assumed) | ~60 days of M5 | near real time, unofficial | **verified** (FX only) |
| **OANDA v20** practice | free demo account | yes (`XAU_USD`) | yes | yes | real bid/ask candles + pricing | <= 5000 candles/request | stream max 4 prices/s; this project polls | adapter unit-tested against documented JSON; **not verified live** |
| **Twelve Data** | free: 8 credits/min, 800/day; paid from $79 | gold listed as forex/metal, **free-plan access unconfirmed** | yes | yes | no (mid, 1-min stream) | up to 5000 bars/request | polling | adapter unverified |
| **MetaTrader 5** | free with broker account | broker-specific symbol | yes | yes | bid + per-bar spread | broker-limited | terminal bridge | adapter unverified, Windows only |
| Forex Factory weekly JSON (calendar) | free | - | - | - | - | current week only | - | **verified reachable**, terms unverified |
| Trading Economics calendar | pricing page not found in review | - | - | - | - | - | - | not integrated |

## Notes and caveats

- **Dukascopy** pads closed periods with flat zero-volume candles; the adapter drops them. The server throttles with HTTP 503;
  downloads use 4 workers, exponential backoff and a local day-file cache (`data/cache/dukascopy`). Terms: public delayed data for
  personal/research use, do not redistribute.
- **Yahoo** is an unofficial endpoint and can change or rate-limit without notice. It has no spot gold, so XAU/USD needs another live source.
- **OANDA** v20 is available to all OANDA divisions except OANDA Global Markets and OANDA TMS BROKERS S.A. (their docs). Practice host
  `api-fxpractice.oanda.com`. Create a token in the account portal ("Manage API Access"), then set `OANDA_TOKEN`, `OANDA_ACCOUNT_ID`.
- **Twelve Data minute-midpoint data is not broker-grade bid/ask**: spread costs are approximated, so scalping results from it are
  optimistic by construction.
- Weekend/holiday handling: markets are modelled as closed Friday 17:00 to Sunday 17:00 New York (gold re-opens 18:00 and has a daily
  17:00-18:00 break). Holidays are not modelled; they appear as reported gaps.

## Recommended setup

1. Free OANDA practice account -> token in `.env` -> `HISTORY_PROVIDER=oanda` (or `FOREX_PROVIDER=oanda` to keep Dukascopy history) for real-time gold with true spreads.
2. Keep Dukascopy for deep research history (more regimes = more statistical power).
3. Budget: **0 USD** for everything above. Paid data only if you decide later; none is required.
