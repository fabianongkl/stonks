"""Run one full daily scan.  Entry point:

    python run_scan.py                 # full universe (~5,000 stocks)
    python run_scan.py --sample 100    # quick test on a random subsample
    python run_scan.py --no-cache      # force re-download of all data

Pipeline: universe -> fundamentals (SEC EDGAR) -> prices (Yahoo) -> factor
scores -> SQLite record -> outcome evaluation of past scans -> weight
learning -> HTML dashboard + journal entry.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import numpy as np
import pandas as pd

from screener import config, db, factors, learning, report, universe
from screener.data import fundamentals, insider, prices, sectors

log = logging.getLogger("scan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="scan only N randomly chosen stocks (testing)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore cached universe/price/fundamental data")
    ap.add_argument("--skip-dashboard", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    today = date.today().isoformat()
    use_cache = not args.no_cache

    # 1. Universe -----------------------------------------------------------
    uni = universe.build_universe(use_cache=use_cache)
    universe_size = len(uni)
    if args.sample:
        uni = uni.sample(min(args.sample, len(uni)), random_state=42)
        log.info("SAMPLE MODE: scanning %d of %d stocks", len(uni), universe_size)

    # 2. Sectors (SIC codes; cached permanently after the first run) --------
    sec = sectors.get_sectors(uni["cik"].tolist())
    uni = uni.merge(sec[["cik", "sector"]], on="cik", how="left")
    uni["sector"] = uni["sector"].fillna("Other")

    # 3. Fundamentals (bulk EDGAR frames — see data/fundamentals.py) --------
    funda = fundamentals.fetch_fundamentals(use_cache=use_cache)

    # 4. Prices -------------------------------------------------------------
    px = prices.fetch_prices(uni["symbol"].tolist(), use_cache=use_cache)
    if px.empty:
        log.error("No price data retrieved — aborting scan.")
        return 1

    # 5. Metrics ------------------------------------------------------------
    pm = factors.price_metrics(px)
    df = uni.merge(pm, on="symbol", how="inner")
    # One row per company: multi-class listings (GOOG/GOOGL) share a CIK and
    # would otherwise be scored twice; keep the most liquid class.
    df = (df.sort_values("dollar_volume", ascending=False)
            .drop_duplicates("cik").reset_index(drop=True))
    df = df.merge(funda, left_on="cik", right_index=True, how="left")

    # 6. Tradability filter (documented in config.py / METHODOLOGY.md) ------
    before = len(df)
    df = df[df["last_close"] >= config.MIN_PRICE]
    df = df[df["dollar_volume"].fillna(0) >= config.MIN_DOLLAR_VOLUME]

    # 6b. Targeted companyfacts fallback for tradable stocks the bulk frames
    #     missed (odd fiscal years, multi-class share structures)
    need = df[df["shares_out"].isna() | df["assets"].isna()
              | (df["net_income"].isna() & df["revenue"].isna())]["cik"].tolist()
    if need:
        fill = fundamentals.companyfacts_fill(
            funda.reindex(df["cik"].unique()), need)
        fund_cols = [c for c in fill.columns if c in funda.columns]
        df = df.drop(columns=fund_cols).merge(
            fill[fund_cols], left_on="cik", right_index=True, how="left")

    df["market_cap"] = df["last_close"] * df["shares_out"]
    df = df[~(df["market_cap"] < config.MIN_MARKET_CAP)]   # keeps NaN mcap
    log.info("Tradability filter: %d -> %d stocks", before, len(df))
    if len(df) < 30:
        log.error("Too few stocks after filtering — aborting scan.")
        return 1

    # 6c. Insider transactions (net open-market buying; skipped gracefully
    #     if the SEC data set is unavailable)
    ins = insider.fetch_insider_net()
    if not ins.empty:
        df["insider_net"] = df["cik"].map(ins).fillna(0.0)

    # 7. Score --------------------------------------------------------------
    df = factors.fundamental_metrics(df)
    conn = db.connect()
    weights = db.get_current_weights(conn)
    scored = factors.score(df, weights)
    scored = scored.rename(columns={"last_close": "close"})

    # 8. Persist ------------------------------------------------------------
    scan_id = db.save_scan(conn, today, universe_size, scored, weights,
                           notes="sample run" if args.sample else "")
    log.info("Scan %d saved: %s, %d scored, %d with full factor coverage",
             scan_id, today, len(scored), int((scored['n_factors'] == 4).sum()))

    # 9. Evaluate matured past scans & maybe learn --------------------------
    # Latest close from the FULL price panel (not just scored stocks), so
    # stocks that fell below today's tradability filters still get honest
    # outcome marks instead of vanishing from the evaluation.
    latest_close = (px.sort_values("date").groupby("symbol")["close"].last())
    learning.update_outcomes(conn, latest_close, today)
    new_w = learning.maybe_update_weights(conn)
    if new_w:
        log.info("Factor weights adapted: %s", new_w)

    # 10. Report ------------------------------------------------------------
    report.write_journal_entry(conn, scan_id, today)
    if not args.skip_dashboard:
        path = report.generate_dashboard(conn, scan_id)
        log.info("Dashboard written: %s", path)

    conn.close()
    print(f"\nScan complete. {len(scored)} stocks scored. "
          f"Dashboard: {config.DASHBOARD_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
