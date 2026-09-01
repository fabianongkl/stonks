"""Track the Claude's Picks hypothetical portfolio.  Run AFTER run_scan.py:

    python track_portfolio.py            # daily: mark to market, update page
    python track_portfolio.py --init     # one-time: deploy the $10,000
    python track_portfolio.py --review   # monthly: apply the replacement rule

Strategy and rationale: docs/PORTFOLIO_EXPERIMENT.md
"""

from __future__ import annotations

import argparse
import logging
import sys

from screener import pf_report, portfolio


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="seed the initial portfolio")
    ap.add_argument("--review", action="store_true", help="apply monthly replacement rule")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    conn = portfolio.connect()

    if args.init:
        portfolio.init_portfolio(conn)
    elif args.review:
        actions = portfolio.review(conn)
        for a in actions:
            print("  ", a)
        if not actions:
            print("Review: all holdings still within rank tolerance — no trades.")

    snap = portfolio.snapshot(conn)
    page = pf_report.generate(conn)

    pos = portfolio.position_table(conn)
    print(f"\n{snap['date']}  positions ${snap['positions_value']:,.2f}"
          f" + cash ${snap['cash']:,.2f} = total ${snap['total']:,.2f}"
          f"  (P&L {snap['total'] - portfolio.START_CASH:+,.2f})")
    if not pos.empty:
        print(pos.to_string(index=False))
    print(f"\nPortfolio page: {page}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
