"""Track the hypothetical portfolio books.  Run AFTER run_scan.py:

    python track_portfolio.py                      # daily: snapshot all books
    python track_portfolio.py --init               # one-time: seed core book
    python track_portfolio.py --init --book aggressive   # seed a book
    python track_portfolio.py --review             # monthly rule, all books
    python track_portfolio.py --review --book core # monthly rule, one book

Strategy and rationale: docs/PORTFOLIO_EXPERIMENT.md
"""

from __future__ import annotations

import argparse
import logging
import sys

from screener import pf_report, portfolio


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="seed a book's initial positions")
    ap.add_argument("--review", action="store_true", help="apply monthly replacement rule")
    ap.add_argument("--book", choices=list(portfolio.BOOKS), default=None,
                    help="restrict --init/--review to one book (default: core for "
                         "--init, all books for --review)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    conn = portfolio.connect()

    if args.init:
        portfolio.init_portfolio(conn, args.book or "core")
    elif args.review:
        books = [args.book] if args.book else list(portfolio.BOOKS)
        for b in books:
            if portfolio.holdings(conn, b).empty:
                continue
            actions = portfolio.review(conn, b)
            for a in actions:
                print(f"  [{b}]", a)
            if not actions:
                print(f"Review [{b}]: all holdings within rank tolerance — no trades.")

    # ritual book: mechanical January rotation, fires automatically on the
    # first scan of a new calendar year (no-op otherwise)
    if not portfolio.holdings(conn, "ritual").empty:
        for a in portfolio.ritual_rotate_if_due(conn):
            print("  [ritual]", a)

    spy = portfolio.spy_close()
    for b in portfolio.BOOKS:
        if portfolio.holdings(conn, b).empty:
            continue
        snap = portfolio.snapshot(conn, b, spy=spy)
        cfg = portfolio.BOOKS[b]
        pos = portfolio.position_table(conn, b)
        print(f"\n[{cfg['label']}] {snap['date']}  positions "
              f"${snap['positions_value']:,.2f} + cash ${snap['cash']:,.2f} "
              f"= total ${snap['total']:,.2f}  "
              f"(P&L {snap['total'] - cfg['start_cash']:+,.2f})")
        if not pos.empty:
            print(pos.to_string(index=False))

    for page in pf_report.generate_all(conn):
        print("page:", page)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
