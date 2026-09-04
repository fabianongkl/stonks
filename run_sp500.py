"""Generate the S&P 500 Lens page (dashboard/sp500.html).  Run after
run_scan.py — additive module; touches nothing in the core pipeline.

    python run_sp500.py
"""

from __future__ import annotations

import logging
import sys

from screener import db, sp500_study


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    conn = db.connect()
    print("S&P 500 page:", sp500_study.generate(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
