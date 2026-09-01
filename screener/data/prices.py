"""Daily price history for the whole universe.

Source: Yahoo Finance via the `yfinance` library — the de-facto standard free
price source used across open-source finance.  Data is end-of-day OHLCV.
Limitations are documented in docs/DATA_SOURCES.md (unofficial API, occasional
gaps for tiny stocks; fine for daily screening, not for live trading).

We download in batches and cache the full panel to parquet, keyed by calendar
day, so a re-run on the same day costs nothing.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from .. import config

log = logging.getLogger(__name__)


def fetch_prices(symbols: list[str], use_cache: bool = True) -> pd.DataFrame:
    """Return a long DataFrame [symbol, date, close, volume] covering
    ~PRICE_LOOKBACK_DAYS calendar days, adjusted for splits/dividends.
    """
    cache = config.CACHE_DIR / f"prices_{date.today().isoformat()}.parquet"
    if use_cache and cache.exists():
        df = pd.read_parquet(cache)
        have = set(df["symbol"].unique())
        missing = [s for s in symbols if s not in have]
        # Cache may have been built from a sample run; top up if needed.
        if not missing:
            return df[df["symbol"].isin(symbols)]
        log.info("Price cache hit for %d symbols; fetching %d more",
                 len(have), len(missing))
        extra = _download(missing)
        df = pd.concat([df, extra], ignore_index=True).drop_duplicates(["symbol", "date"])
        df.to_parquet(cache, index=False)
        return df[df["symbol"].isin(symbols)]

    df = _download(symbols)
    df.to_parquet(cache, index=False)
    return df


def _download(symbols: list[str]) -> pd.DataFrame:
    start = date.today() - timedelta(days=config.PRICE_LOOKBACK_DAYS + 40)
    chunks = [symbols[i:i + config.YF_BATCH_SIZE]
              for i in range(0, len(symbols), config.YF_BATCH_SIZE)]
    out = []
    for i, chunk in enumerate(chunks, 1):
        log.info("Downloading prices: batch %d/%d (%d tickers)", i, len(chunks), len(chunk))
        try:
            raw = yf.download(
                tickers=chunk,
                start=start.isoformat(),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:  # network hiccup on one batch shouldn't kill the scan
            log.warning("Batch %d failed (%s); skipping", i, e)
            continue
        if raw is None or raw.empty:
            continue
        out.append(_to_long(raw, chunk))
    if not out:
        return pd.DataFrame(columns=["symbol", "date", "close", "volume"])
    df = pd.concat(out, ignore_index=True)
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    return df


def _to_long(raw: pd.DataFrame, chunk: list[str]) -> pd.DataFrame:
    rows = []
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in chunk:
            if sym not in raw.columns.get_level_values(0):
                continue
            sub = raw[sym]
            if "Close" not in sub.columns:
                continue
            rows.append(pd.DataFrame({
                "symbol": sym,
                "date": sub.index.date,
                "close": sub["Close"].to_numpy(dtype=float),
                "volume": sub.get("Volume", pd.Series(np.nan, index=sub.index)).to_numpy(dtype=float),
            }))
    else:  # single-ticker chunk comes back flat
        rows.append(pd.DataFrame({
            "symbol": chunk[0],
            "date": raw.index.date,
            "close": raw["Close"].to_numpy(dtype=float),
            "volume": raw.get("Volume", pd.Series(np.nan, index=raw.index)).to_numpy(dtype=float),
        }))
    if not rows:
        return pd.DataFrame(columns=["symbol", "date", "close", "volume"])
    return pd.concat(rows, ignore_index=True)
