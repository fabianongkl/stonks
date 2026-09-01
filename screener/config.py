"""Central configuration for the screener.

Everything tunable lives here so users of the open-source project can
adjust behaviour without touching the pipeline code.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — everything the screener produces lives under the Screener folder.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "screener.db"
DASHBOARD_DIR = ROOT / "dashboard"
JOURNAL_DIR = ROOT / "journal"

for _p in (DATA_DIR, CACHE_DIR, DASHBOARD_DIR, JOURNAL_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# HTTP etiquette.
# SEC EDGAR requires a User-Agent of the form "Name contact@email" and
# returns 403 otherwise (SEC fair-use policy asks for a real contact).
# Set the SEC_CONTACT environment variable to your email — in GitHub Actions
# this comes from a repository secret, keeping the address out of public code.
# ---------------------------------------------------------------------------
import os as _os

SEC_USER_AGENT = (f"OpenScreener/0.1 "
                  f"{_os.environ.get('SEC_CONTACT', 'admin@openscreener.example')}")
SEC_MAX_REQ_PER_SEC = 8  # SEC fair-use limit is 10/s; stay under it.

# ---------------------------------------------------------------------------
# Universe filters — which stocks are even considered.
# These are *tradability* filters, not opinions about quality.
# ---------------------------------------------------------------------------
MIN_PRICE = 2.0            # exclude sub-$2 stocks: unreliable data, huge spreads
MIN_DOLLAR_VOLUME = 500_000  # median daily $ volume; below this, quotes are noise
MIN_MARKET_CAP = 50e6      # below ~$50M, EDGAR data is sparse and manipulation risk high

# ---------------------------------------------------------------------------
# Factor model defaults.
# Starting weights follow the rough proportions used in published multi-factor
# strategies (see docs/METHODOLOGY.md).  The learning module adjusts these
# over time based on realised predictive power (information coefficients).
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "value": 0.25,
    "quality": 0.30,
    "momentum": 0.30,
    "low_vol": 0.15,
}

# Sector-neutral scoring: rank metrics within sector so the composite compares
# like with like (banks vs banks, software vs software) instead of making
# structural sector bets.  Groups smaller than MIN_SECTOR_GROUP fall back to
# global ranking.
SECTOR_NEUTRAL = True
MIN_SECTOR_GROUP = 30

# ---------------------------------------------------------------------------
# Learning / evaluation settings.
# ---------------------------------------------------------------------------
EVAL_HORIZONS_DAYS = [21, 63, 126]   # ~1, 3, 6 months of trading days
PRIMARY_HORIZON = 63                 # weight learning keys off 3-month results
# Daily scans with 63-day horizons overlap almost entirely — 8 consecutive
# matured scans are ~1.5 independent observations, not 8.  The learning
# trigger therefore counts only NON-OVERLAPPING evaluation windows (scans at
# least PRIMARY_HORIZON trading days apart), so weights can't start chasing
# one market regime observed many times.
MIN_INDEPENDENT_EVALS = 6            # ≈ 1.5 years of distinct quarters
WEIGHT_FLOOR = 0.05                  # no factor's weight ever drops below this
WEIGHT_LEARNING_RATE = 0.25          # how far weights move toward IC-implied weights
TOP_DECILE = 0.10                    # "picks" tracked = top 10% by composite score

# ---------------------------------------------------------------------------
# Price data.
# ---------------------------------------------------------------------------
PRICE_LOOKBACK_DAYS = 420   # ~14 months of BUSINESS days back for momentum calc
YF_BATCH_SIZE = 250         # tickers per yfinance bulk request
