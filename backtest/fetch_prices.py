"""Download ~6.5 years of daily prices for the whole universe (plus SPY).

    python -m backtest.fetch_prices

Cached to backtest/cache/prices_hist.parquet; safe to re-run (resumes by
skipping symbols already cached).  Yahoo end-of-day data — the same source
and limitations as the live screener (see docs/DATA_SOURCES.md).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yfinance as yf

from screener import universe
from . import config_bt

log = logging.getLogger("bt.prices")

OUT = config_bt.CACHE / "prices_hist.parquet"
BATCH = 200


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    uni = universe.build_universe()
    symbols = uni["symbol"].tolist() + [config_bt.BENCH]

    done = pd.DataFrame(columns=["symbol", "date", "close", "volume"])
    if OUT.exists():
        done = pd.read_parquet(OUT)
        have = set(done["symbol"].unique())
        symbols = [s for s in symbols if s not in have]
        log.info("Resuming: %d symbols cached, %d to fetch", len(have), len(symbols))

    chunks = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
    frames = [done] if not done.empty else []
    for i, chunk in enumerate(chunks, 1):
        log.info("history batch %d/%d (%d tickers)", i, len(chunks), len(chunk))
        try:
            raw = yf.download(tickers=chunk, start=config_bt.PRICE_START,
                              auto_adjust=True, progress=False,
                              group_by="ticker", threads=True)
        except Exception as e:
            log.warning("batch %d failed: %s", i, e)
            continue
        if raw is None or raw.empty:
            continue
        rows = []
        if isinstance(raw.columns, pd.MultiIndex):
            for sym in chunk:
                if sym not in raw.columns.get_level_values(0):
                    continue
                sub = raw[sym]
                if "Close" not in sub:
                    continue
                rows.append(pd.DataFrame({
                    "symbol": sym, "date": sub.index.date,
                    "close": sub["Close"].to_numpy(float),
                    "volume": sub.get("Volume", pd.Series(np.nan, index=sub.index)).to_numpy(float),
                }))
        else:
            rows.append(pd.DataFrame({
                "symbol": chunk[0], "date": raw.index.date,
                "close": raw["Close"].to_numpy(float),
                "volume": raw.get("Volume", pd.Series(np.nan, index=raw.index)).to_numpy(float),
            }))
        if rows:
            part = pd.concat(rows, ignore_index=True).dropna(subset=["close"])
            part = part[part["close"] > 0]
            frames.append(part)
        if i % 5 == 0 or i == len(chunks):    # checkpoint
            pd.concat(frames, ignore_index=True).to_parquet(OUT, index=False)
            log.info("checkpointed after batch %d", i)

    total = pd.concat(frames, ignore_index=True)
    total.to_parquet(OUT, index=False)
    log.info("DONE: %d symbols, %d rows, %s..%s", total["symbol"].nunique(),
             len(total), total["date"].min(), total["date"].max())


if __name__ == "__main__":
    main()
