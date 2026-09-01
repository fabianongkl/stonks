"""Backtest parameters."""

from pathlib import Path

BT_ROOT = Path(__file__).resolve().parent
CACHE = BT_ROOT / "cache"
RESULTS = BT_ROOT / "results"
for _p in (CACHE, RESULTS):
    _p.mkdir(exist_ok=True)

PRICE_START = "2020-03-01"      # ~18 months before first rebalance (momentum warm-up)
FIRST_REBALANCE = "2021-09-01"  # 5 years of monthly rebalances
LAST_REBALANCE = "2026-08-01"

BENCH = "SPY"
