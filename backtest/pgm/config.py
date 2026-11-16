"""PGM backtest parameters. Mirrors docs/PGM_SCREEN.md; frozen 2026-11-16."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT.parent / "cache"          # backtest/cache (gitignored)
RESULTS = ROOT / "results"
for _p in (CACHE, RESULTS):
    _p.mkdir(exist_ok=True)

FACTS = CACHE / "pgm_facts.parquet"
PRICES = CACHE / "pgm_prices.parquet"
UNIVERSE = CACHE / "pgm_universe.parquet"

PRICE_START = "2011-06-01"
FIRST_REBAL = "2013-02-28"
HOLDOUT_START = "2023-05-31"
REBAL_MONTHS = (2, 5, 8, 11)

# universe (section 4)
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 1e6
MIN_MCAP = 300e6
MIN_HISTORY = 252
FIN_SIC = (6000, 6799)
MAX_FUND_AGE_DAYS = 200

# signals (section 6)
WEIGHTS = {
    "gpa": 0.125,
    "d_ocf": 0.125,
    "rev_g": 0.10,
    "rev_accel": 0.075,
    "margin_exp": 0.075,
    "sue": 0.10,
    "mom_12_1": 0.20,
    "near_high": 0.10,
    "low_issue": 0.10,
}
assert abs(sum(WEIGHTS.values()) - 1) < 1e-9

# portfolios (section 7)
BOOKS = {"broad": 25, "conc": 8}
BUFFER_MULT = 2
START_CASH = 3000.0
COMM_PER_SH = 0.005
COMM_MIN = 1.0
SLIP_PER_SH = 0.01
ONE_SHARE_MAX_MULT = 1.5
UNCON_COST_BPS = 5.0
