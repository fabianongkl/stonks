"""Run the historical backtest end-to-end (after the two fetch steps):

    python -m backtest.fetch_prices   # ~25 min, once
    python -m backtest.fetch_pit      # ~35 min, once
    python run_backtest.py

Outputs:
    backtest/results/monthly_results.parquet
    backtest/results/REPORT.md          (read the caveats!)
    dashboard/backtest.html
"""

from __future__ import annotations

import logging
import sys

import pandas as pd

from backtest import bt_report, config_bt, engine


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    res = engine.run()
    if res.empty:
        print("No results produced — check fetch steps completed.")
        return 1

    facts = pd.read_parquet(config_bt.CACHE / "pit_facts.parquet")
    px = pd.read_parquet(config_bt.CACHE / "prices_hist.parquet",
                         columns=["symbol", "date"])
    px["year"] = pd.to_datetime(px["date"]).dt.year
    priced_by_year = px.groupby("year")["symbol"].nunique().to_dict()

    s = bt_report.summarize(res, facts, priced_by_year)
    print("\nReport:", bt_report.write_report(s))
    print("Chart page:", bt_report.write_html(s))
    print(f"\nHeadline (survivorship-biased): top decile "
          f"{s['ann_top']*100:+.1f}%/yr vs median {s['ann_median']*100:+.1f}%/yr "
          f"-> spread {s['ann_spread']*100:+.1f}%/yr (t={s['spread_tstat']:.2f}); "
          f"SPY {s['ann_spy']*100:+.1f}%/yr; sim book ${s['book_final']:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
