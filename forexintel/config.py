"""Central configuration: instrument specs, risk policy and environment settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_TF = "M5"
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD")


@dataclass(frozen=True)
class Instrument:
    symbol: str
    display: str
    asset_class: str
    quote_precision: int
    tick_size: float
    pip_size: float
    contract_size: float          # units per 1.0 lot
    assumed_spread: float         # price units; used ONLY when a provider has no bid/ask
    commission_rt: float          # round-turn commission in price units (retail ECN-style)
    min_stop: float               # minimum stop distance in price units
    news_currencies: tuple
    provider_symbols: dict = field(default_factory=dict)


INSTRUMENTS: dict[str, Instrument] = {
    "XAUUSD": Instrument(
        "XAUUSD", "XAU/USD", "metal", 2, 0.01, 0.1, 100.0, 0.35, 0.07, 0.80, ("USD",),
        {"dukascopy": "XAUUSD", "oanda": "XAU_USD", "twelvedata": "XAU/USD", "mt5": "XAUUSD", "yahoo": None, "demo": "XAUUSD"},
    ),
    "EURUSD": Instrument(
        "EURUSD", "EUR/USD", "fx", 5, 0.00001, 0.0001, 100000.0, 0.00010, 0.00007, 0.00040, ("EUR", "USD"),
        {"dukascopy": "EURUSD", "oanda": "EUR_USD", "twelvedata": "EUR/USD", "mt5": "EURUSD", "yahoo": "EURUSD=X", "demo": "EURUSD"},
    ),
    "GBPUSD": Instrument(
        "GBPUSD", "GBP/USD", "fx", 5, 0.00001, 0.0001, 100000.0, 0.00014, 0.00007, 0.00050, ("GBP", "USD"),
        {"dukascopy": "GBPUSD", "oanda": "GBP_USD", "twelvedata": "GBP/USD", "mt5": "GBPUSD", "yahoo": "GBPUSD=X", "demo": "GBPUSD"},
    ),
}


def normalize_symbol(s: str) -> str:
    s = s.upper().replace("/", "").replace("_", "").replace("-", "")
    if s not in INSTRUMENTS:
        raise ValueError(f"Unsupported symbol '{s}'. Supported: {', '.join(SYMBOLS)}")
    return s


def _f(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    history_provider: str = "dukascopy"     # deep free bid/ask history (lagging ~1 day)
    live_provider: str = "yahoo"            # optional live tail: yahoo|oanda|twelvedata|mt5|none
    twelvedata_key: str = ""
    oanda_token: str = ""
    oanda_account: str = ""
    oanda_env: str = "practice"
    api_token: str = ""
    models_dir: Path = ROOT / "models"
    data_dir: Path = ROOT / "data"

    # paper / risk policy (research only, never real orders)
    paper_equity: float = 10000.0
    risk_per_trade_pct: float = 0.5
    daily_loss_limit_pct: float = 2.0
    news_unavailable_policy: str = "warn"   # warn | block
    news_pre_minutes: int = 30
    news_post_minutes: int = 15

    # trade plan / label definition
    horizon_bars: int = 12                  # M5 bars a trade may stay open
    tp1_r: float = 1.0
    tp2_r: float = 1.8
    tp3_r: float = 2.8
    sl_atr_mult: float = 1.2
    sl_atr_min: float = 0.8
    sl_atr_max: float = 2.5
    signal_expiry_bars: int = 2
    slippage_spread_frac: float = 0.25      # slippage per fill as a fraction of spread

    # gates
    max_spread_atr: float = 0.25            # block when spread > 25% of ATR(14) M5
    stale_after_bars: int = 3               # last complete M5 older than this => stale
    max_recent_gap_bars: int = 3            # tolerated missing M5 bars in last 200
    min_samples: int = 50                   # minimum validated trades before authorizing a strategy
    min_ev_r: float = 0.05                  # minimum net expectancy (R) to authorize
    strong_ev_r: float = 0.20

    @staticmethod
    def from_env() -> "Settings":
        data_dir = ROOT / "data"
        url = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{(data_dir / 'forex.db').as_posix()}"
        return Settings(
            database_url=url,
            history_provider=os.getenv("HISTORY_PROVIDER", "dukascopy").strip().lower(),
            live_provider=os.getenv("FOREX_PROVIDER", "yahoo").strip().lower(),
            twelvedata_key=os.getenv("TWELVEDATA_API_KEY", "").strip(),
            oanda_token=os.getenv("OANDA_TOKEN", "").strip(),
            oanda_account=os.getenv("OANDA_ACCOUNT_ID", "").strip(),
            oanda_env=os.getenv("OANDA_ENV", "practice").strip().lower(),
            api_token=os.getenv("API_TOKEN", "").strip(),
            paper_equity=_f("PAPER_EQUITY", 10000.0),
            risk_per_trade_pct=_f("RISK_PER_TRADE_PCT", 0.5),
            daily_loss_limit_pct=_f("DAILY_LOSS_LIMIT_PCT", 2.0),
            news_unavailable_policy=os.getenv("NEWS_UNAVAILABLE_POLICY", "warn").strip().lower(),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(s: Settings | None) -> None:
    """Override settings (tests). Pass None to reload from the environment."""
    global _settings
    _settings = s


def override(**kw) -> Settings:
    s = replace(get_settings(), **kw)
    set_settings(s)
    return s
