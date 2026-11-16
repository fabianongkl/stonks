"""Download daily prices with split history for the PGM backtest.

    python -m backtest.pgm.fetch_prices

Yahoo via yfinance, auto_adjust=False + actions=True, so we keep
Close (split-adjusted), Adj Close (split+dividend) and Stock Splits. The
unadjusted historical price is rebuilt in signals.py. Resumable.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import yfinance as yf

from . import config as C

log = logging.getLogger("pgm.prices")
BATCH = 100
EXTRA = ["SPY", "SHY"]


def _rows(sym: str, sub: pd.DataFrame) -> pd.DataFrame | None:
    if "Close" not in sub or sub["Close"].dropna().empty:
        return None
    g = lambda c: sub[c].to_numpy(float) if c in sub else np.full(len(sub), np.nan)
    df = pd.DataFrame({"symbol": sym, "date": sub.index.date,
                       "close": g("Close"), "adj": g("Adj Close"),
                       "volume": g("Volume"), "split": g("Stock Splits")})
    return df.dropna(subset=["close", "adj"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    uni = pd.read_parquet(C.UNIVERSE)
    symbols = EXTRA + uni["symbol"].tolist()
    frames, have = [], set()
    if C.PRICES.exists():
        prev = pd.read_parquet(C.PRICES)
        have = set(prev["symbol"].unique())
        frames.append(prev)
    todo = [s for s in symbols if s not in have]
    log.info("%d symbols, %d cached, %d to fetch", len(symbols), len(have), len(todo))
    chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    for i, chunk in enumerate(chunks, 1):
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(tickers=chunk, start=C.PRICE_START,
                                  auto_adjust=False, actions=True,
                                  progress=False, group_by="ticker", threads=True)
                break
            except Exception as e:
                log.warning("batch %d attempt %d failed: %s", i, attempt, e)
                time.sleep(20 * (attempt + 1))
        if raw is None or raw.empty:
            continue
        parts = []
        for sym in chunk:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.get_level_values(0):
                    continue
                r = _rows(sym, raw[sym])
            else:
                r = _rows(sym, raw)
            if r is not None:
                parts.append(r)
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))
        if i % 5 == 0 or i == len(chunks):
            pd.concat(frames, ignore_index=True).to_parquet(C.PRICES, index=False)
            log.info("batch %d/%d checkpointed", i, len(chunks))
    log.info("done")


if __name__ == "__main__":
    main()
