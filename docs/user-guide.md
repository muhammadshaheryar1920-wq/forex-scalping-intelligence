# User guide: running the platform locally and using the browser dashboard

This bot is for **research and paper trading only**. It never places real orders. Most of the time its answer is **WAIT**,
because tests on real data found no strategy that stays profitable after spread, slippage and commission. That is the
system being honest, not a fault.

---

## Part 1. Running it on your laptop (Windows)

### 1.1 One-time setup

Open **PowerShell** and run each line:

```powershell
cd F:\Trading_Bot
python -m venv .venv                      # only if the .venv folder does not exist yet
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1              # prompt now starts with (.venv)
pip install -r requirements.txt
copy .env.example .env                    # optional; the bot runs without any keys
```

You must run `Activate.ps1` in every new PowerShell window before using `python main.py ...`.

### 1.2 First run: check, then download data

```powershell
python main.py doctor                     # health check: packages, database, providers, data, models
python main.py sync --all --days 90       # download 90 days of free bid/ask history (a few minutes)
```

- `doctor` prints `PASS`, `WARN` or `FAIL` per item. Warnings such as "no model" or "calendar unavailable" are normal at first.
- `sync` can look quiet for a minute or two: the free Dukascopy server throttles downloads and the bot retries politely. Wait.
- Days that fail are retried automatically on the next sync.

### 1.3 Get a first answer

```powershell
python main.py signal --symbol EURUSD --auto-sync
```

`--auto-sync` first downloads anything missing (history plus the live tail), then analyses. Without it the bot uses only what is already stored.

### 1.4 Start the dashboard

```powershell
python main.py dashboard
```

This starts the server and opens **http://127.0.0.1:8000** in your browser. Keep the PowerShell window open: closing it stops the dashboard.
To start without opening a browser use `python main.py serve`. Stop with **Ctrl+C**. The built-in API documentation is at http://127.0.0.1:8000/docs.

### 1.4a Which data feeds you get without keys

| Symbol | History | Live prices |
|---|---|---|
| EUR/USD, GBP/USD | Dukascopy (real bid/ask, about 1 day behind) | Yahoo (mid price only, spread is an assumption) |
| XAU/USD (gold) | Dukascopy | **None for free**. Gold shows `FEED_STALE`. For live gold add a free OANDA practice token. |

To add OANDA, edit `.env`:

```
OANDA_TOKEN=your_practice_token
OANDA_ACCOUNT_ID=your_account_id
FOREX_PROVIDER=oanda
```

Then run `python main.py doctor --deep`. The OANDA connection is written from the official docs but has not been tested against a real account, so check the doctor output.

### 1.5 All commands

| Command | What it does |
|---|---|
| `python main.py doctor [--deep]` | Health check. `--deep` also tests the internet connection to each data provider. |
| `python main.py sync --all` or `--symbol XAUUSD` | Download missing candles. Options: `--days 365` (go further back), `--provider yahoo` (one source only). `fetch` is the same command. |
| `python main.py signal --symbol EURUSD --auto-sync` | Latest analysis. `--all` for all three symbols, `--json` for raw output. |
| `python main.py backtest --all --walk-forward` | Replays every strategy on stored history with realistic costs and prints win rate, profit per trade and drawdown. |
| `python main.py train --symbol XAUUSD` | Trains and tests the machine-learning models. Nothing is switched on automatically. |
| `python main.py model-status --all` | Lists saved models and whether any is active. |
| `python main.py model-approve <version>` | You approve a model that passed testing. Rejected models cannot be approved. |
| `python main.py model-rollback --symbol XAUUSD` | Switch the active model off (back to the previous one, or to rules only). |
| `python main.py monitor --all --on-candle-close` | Analyses automatically after every 5-minute candle closes while the laptop is on. `--once` runs a single cycle. |
| `python main.py paper` | Prints the paper-trading summary. |
| `python main.py calendar` | Refreshes the news calendar and lists upcoming high-impact events. |
| `python main.py serve` / `dashboard` | Starts the web server / starts it and opens the browser. |

Exit codes: `0` fine, `1` error, `2` data blocked (stale feed, missing data or credentials).

### 1.6 Daily routine

1. `.\.venv\Scripts\Activate.ps1`
2. `python main.py dashboard`
3. In the browser click **Sync all (job)**, then **Start monitor** and leave it running.
4. When you are done, press Ctrl+C in PowerShell.

The bot only works while the laptop is on. After days offline, run a sync: missed candles are re-downloaded. Prices that happened while the laptop was off between candles cannot be recovered.

---

## Part 2. The browser dashboard, function by function

### 2.1 Top bar (on every page)

| Item | Meaning |
|---|---|
| **STREAM** badge | Live connection to the server. "LIVE STREAM" is green, "RECONNECTING" yellow. It reconnects by itself. |
| **UTC / Asia/Karachi** drop-down | Changes how all times are displayed. The database always stores UTC. Your choice is remembered. |
| Clock | Current time in the chosen timezone. |
| **Token** button | Only needed if you set `API_TOKEN` in `.env`. It asks for the token and remembers it for this browser session. |
| Menu row | Eight pages. The active one is blue. On a phone the menu scrolls sideways. |

### 2.2 Status badges you will see

| Badge | Meaning |
|---|---|
| **LIVE** (green) | Data is fresh (last 5-minute candle is only a few minutes old). |
| **MARKET CLOSED - LAST UPDATED** | Weekend or daily break. The numbers are the latest available. |
| **DELAYED - LAST UPDATED** (yellow) | Data is stale, so trading is blocked. For gold without a live key this is normal. |
| **DEGRADED** | Candles are missing in the recent past. Run a sync. |
| **INSUFFICIENT DATA** (red) | Not enough stored history. Run a sync. |
| **DEMO** | Synthetic test data, never real. |
| **MODEL NOT READY** | No approved model exists, so only simple rules are used. This is the normal state today. |

### 2.3 Page 1: Command Center

The home screen with one card per instrument (XAU/USD, EUR/USD, GBP/USD). Each card shows:

- **Bid / Ask and Spread.** If it says "(assumed)", the spread is an estimate because the source gives only a mid price.
- **Feed age.** Minutes since the last closed 5-minute candle, plus the data status.
- **Session / Regime.** Session is Asia, London, New York or the London-NY overlap. Regime is the market state: `TREND_UP`, `TREND_DOWN`, `RANGE`, `HIGH_VOL` or `QUIET`.
- **Forecast.** UP, DOWN or NEUTRAL for the next 6 candles. "P(up)" appears only when a tested, calibrated model is active. Otherwise it shows `NOT_CALIBRATED`: it is a rules-based lean, not a probability.
- **Big action box.** One of **STRONG BUY, BUY, WAIT, SELL, STRONG SELL, CANNOT ASSESS**.
  WAIT means no trade is allowed right now. A forecast of UP can still be WAIT.
- **Trade details** (only when a trade is allowed): strategy, entry zone, stop loss, take profits 1 to 3, risk/reward, expected move in percent, probability, and how long the signal is valid.
- **Red chips** are blockers, the exact reasons no trade is allowed, for example `NO_VALIDATED_EDGE`, `FEED_STALE`, `NEWS_BLACKOUT`, `SPREAD_TOO_WIDE`, `DAILY_CIRCUIT_BREAKER`, `MARKET_CLOSED`. **Yellow chips** are warnings.
- **Short list of reasons** underneath explaining what the bot saw.

Buttons on each card:

| Button | What it does |
|---|---|
| **Get signal** | Re-runs the analysis on stored data and saves it in the Signal Explorer. |
| **Sync + signal** | Downloads missing candles first, then analyses. Takes longer. |
| **Chart** | Opens the Symbol Intelligence page for that instrument. |

Buttons at the top of the page:

| Button | What it does |
|---|---|
| **Sync all (job)** | Starts a background download for all three instruments. A job ID appears in "Recent jobs". |
| **Start monitor** | Starts automatic analysis after every 5-minute candle, as long as the server is running. |
| **Pause monitor** | Stops the monitor. The label next to it shows running or paused and the number of cycles done. |
| **Refresh** | Reloads the three cards. |

**Recent jobs** lists background tasks with status QUEUED, RUNNING, DONE or FAILED. Click **Log** to read what the job did.

### 2.4 Page 2: Signal Explorer

A history of every analysis the bot has made, including the WAIT ones.

- **Filters:** choose a symbol and/or an action (LONG, SHORT, NO_TRADE) and click **Apply**.
- **Export CSV:** downloads the table as `signals.csv`.
- **Columns:** time, symbol, decision, strategy, bias, regime, entry/stop/TP1, probability, expected value in R, blockers, model version ("rules-only" if none).

Use it to check how often the bot says WAIT and why.

### 2.5 Page 3: Symbol Intelligence

A candlestick chart with the bot's market-structure markings.

- **Drop-downs:** pick the symbol and timeframe (M5, M15, H1). Candles shown are always closed ones.
- **Check boxes** switch overlays on and off:
  - **BOS/CHoCH:** arrows where price broke structure (continuation) or changed character (possible reversal).
  - **Sweeps:** circles where price grabbed a swing high or low and came back.
  - **Swing/Asia levels:** dashed lines at the latest swing high and low and the Asia session range.
  - **FVG:** fair value gaps, shown as two dotted lines per zone.
  - **Order blocks:** the candle zone before a strong structural break.
- **Right panel:** regime, session, forecast, decision, the reasons, blockers, every strategy candidate found on the latest bar (and whether it has proven edge), and the forecast for 3, 6 and 12 candles when a model exists.

These markings are chart-pattern definitions that the bot tests, not secret bank data. If the chart area says the library could not load, your internet is off: the chart script comes from a public CDN.

### 2.6 Page 4: Model Lab

Where machine-learning models are trained and judged.

- **Symbol drop-down** selects the instrument.
- **Train (job):** builds a model in the background using the stored history. It takes a few minutes.
- **Sync data (job):** downloads missing data for the selected symbol.
- **Roll back active model:** turns the active model off (asks for confirmation).
- **Data health card:** status, number of stored candles, last candle, recent missing candles, data sources, and how many bars use an assumed spread.
- **Latest trade model card:** version, algorithm, how events were split (train / calibration / validation / test), the rules-only baseline versus the model on the untouched test slice, quality measures (Brier score, AUC) and a note.
  Status **REJECTED** means it did not beat the baseline: it cannot be activated. **CANDIDATE** means it passed and waits for **Approve this model**, which is your decision. **APPROVED** means it is active.
- **Calibration chart:** points close to the dashed diagonal mean the predicted probabilities match reality.
- **Ablation table:** how much the model gets worse when a feature group is removed (a larger drop means that group matters more).
- **All model versions:** the full list with status and date.

Models are never switched on automatically. Today all trade models are REJECTED, so the bot uses rules only.

### 2.7 Page 5: Backtest Analytics

Replays the strategies on history with real bid/ask prices, slippage and commission.

- **Run backtest + walk-forward (job):** runs it in the background (1 to 3 minutes). The page then shows the latest result for the chosen symbol.
- **Top tiles:** number of trades, win rate, net expectancy (average profit per trade in R, where 1 R is the amount risked), profit factor (above 1 is profitable), maximum drawdown.
- **Equity curve:** cumulative R over time. Going up means profit.
- **Strategies table:** each strategy on its own with a 95% range for the win rate.
- **Walk-forward table:** results of the model re-trained on past data and tested on the following unseen period. This is the closest thing to a live test.
- **Breakdown tables:** results by session, regime, month and side (long or short).

Read **Net EV (R)**, not win rate. A negative value means the strategy loses money after costs, however many trades it wins.

### 2.8 Page 6: Paper Journal

Simulated trades only.

- A red notice appears if the **daily circuit breaker** tripped (new simulated trades stop after a 2% simulated loss in a day).
- **Authorized signals** are trades the bot was allowed to take.
- **Shadow candidates** are setups that had no proven edge. They are tracked anyway, so you can see over weeks what they would have done.
- **Positions table:** state PENDING, OPEN, CLOSED or NOFILL, fill price, stop, TP1, exit reason (TP1, SL, TIMEOUT), result in R and simulated dollars.

Positions are filled at the next candle's open on the correct side of the spread, exactly as in the backtest.

### 2.9 Page 7: Economic Events

- **Calendar status:** OK, or CALENDAR_UNAVAILABLE if the free news feed could not be loaded.
- **Refresh calendar:** downloads the week's events.
- **One card per symbol:** a badge CLEAR, BLACKOUT or UNAVAILABLE, and a table of upcoming high and medium impact events with a countdown.
  BLACKOUT means high-impact news is within 30 minutes before or 15 minutes after, so trades are blocked.
  XAU/USD follows USD events, EUR/USD follows EUR and USD, GBP/USD follows GBP and USD.

Backtests do not use news (the free feed has no history).

### 2.10 Page 8: Settings / Health

- **Runtime:** mode (paper only), data providers, whether an API token is required, database state, and whether each key is set or not set. Key values are never shown.
- **Risk policy:** the numbers in force, such as risk per trade, daily loss limit, spread limit and news window.
- **Providers:** for each data source: OK or not, whether it has real bid/ask, whether this project verified it, and which symbols it supports.
- **Instruments:** assumed spread and commission per symbol.
- **Recent audit log:** who did what (syncs, jobs, model approvals).
- **Jobs:** every background task and its log.

To change settings, edit `.env` (or `forexintel/config.py`) and restart the server.

---

## Part 3. How to judge whether it works

1. **Data correct?** Compare bid/ask on the Command Center with a live chart. EUR/USD and GBP/USD should be within a pip or two. Feed age should be under about 10 minutes.
2. **Logic honest?** Every card should show both a forecast and a decision. WAIT with a clear blocker is a valid result.
3. **Evidence?** Backtest Analytics shows the truth about profitability. Today every strategy has negative net EV.
4. **Forward test.** Leave **Start monitor** running for weeks and check the Paper Journal. This is the real test.

## Part 4. Troubleshooting

| Problem | What to do |
|---|---|
| `signal --auto-sync` seems stuck | The Dukascopy server is throttling. Wait 3 to 5 minutes, or press Ctrl+C and run `python main.py signal --symbol EURUSD` without `--auto-sync`. |
| Gold shows `FEED_STALE` | Expected without a live source. Add an OANDA practice token (section 1.4a). |
| "CANNOT ASSESS" / INSUFFICIENT DATA | Run `python main.py sync --all --days 90`. |
| Script blocked when activating the venv | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`, then activate again. |
| `python` not recognised | Activate the venv first (`.\.venv\Scripts\Activate.ps1`). |
| Port 8000 already in use | `python main.py serve --port 8001`, then open http://127.0.0.1:8001. |
| Dashboard asks for a token | You set `API_TOKEN`. Click **Token** and enter it. |
| Chart does not appear | Internet needed for the chart script. Everything else works offline. |
| Anything else | Run `python main.py doctor`, then check Settings / Health and the job logs. |

## Part 5. Safety

- No real-money orders exist anywhere in this code.
- Keep the dashboard on `127.0.0.1`. Do not expose it to the internet.
- Never share your `.env` file. It is excluded from git.
- This is research software, not financial advice. Most short-term traders lose money.
