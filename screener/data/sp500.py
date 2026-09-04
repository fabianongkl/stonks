"""S&P 500 membership and multi-year price history for the index module.

Membership: Wikipedia's "List of S&P 500 companies" — the standard free
source (the index provider's own list sits behind a license).  Cached daily.

IMPORTANT documented bias: this is TODAY'S membership.  Companies that fell
out of the index during past years are invisible to any historical study
built on it, which flatters past "winners" somewhat.  Free point-in-time
membership does not exist; the study page states this caveat prominently.

Prices: ~6.5 years of daily closes for members + SPY, cached weekly locally
(the cloud runner refetches — ~2 batches, about a minute).
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from .. import config
from ..universe import normalize_symbol

log = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HISTORY_START = "2020-03-01"


def fetch_members(use_cache: bool = True) -> pd.DataFrame:
    """DataFrame [symbol, name, gics_sector] of current S&P 500 members."""
    cache = config.CACHE_DIR / f"sp500_{date.today().isoformat()}.parquet"
    if use_cache and cache.exists():
        return pd.read_parquet(cache)
    r = requests.get(WIKI_URL, timeout=60,
                     headers={"User-Agent": config.SEC_USER_AGENT})
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    tbl = next(t for t in tables
               if "Symbol" in t.columns and len(t) > 400)
    date_added = (pd.to_datetime(tbl.get("Date added"), errors="coerce")
                  if "Date added" in tbl.columns else pd.Series(pd.NaT, index=tbl.index))
    out = pd.DataFrame({
        "symbol": tbl["Symbol"].astype(str).map(normalize_symbol),
        "name": tbl["Security"].astype(str),
        "gics_sector": tbl["GICS Sector"].astype(str),
        # when the CURRENT member joined the index — lets historical studies
        # exclude anachronistic members (half the survivorship fix; removed
        # companies remain invisible, stated in the page caveat)
        "date_added": date_added.dt.strftime("%Y-%m-%d"),
    }).drop_duplicates("symbol").reset_index(drop=True)
    log.info("S&P 500 members from Wikipedia: %d", len(out))
    out.to_parquet(cache, index=False)
    return out


def fetch_history(symbols: list[str], use_cache: bool = True,
                  start: str = HISTORY_START, tag: str = "hist") -> pd.DataFrame:
    """Long DataFrame [symbol, date, close] since `start`, incl. SPY."""
    week = date.today().isocalendar()
    cache = config.CACHE_DIR / f"sp500_{tag}_{week.year}w{week.week:02d}.parquet"
    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    import yfinance as yf
    want = list(dict.fromkeys(symbols + ["SPY"]))
    frames = []
    for i in range(0, len(want), 250):
        chunk = want[i:i + 250]
        log.info("S&P history batch %d (%d tickers)", i // 250 + 1, len(chunk))
        try:
            raw = yf.download(tickers=chunk, start=start,
                              auto_adjust=True, progress=False,
                              group_by="ticker", threads=True)
        except Exception as e:
            log.warning("S&P history batch failed: %s", e)
            continue
        if raw is None or raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            for sym in chunk:
                if sym not in raw.columns.get_level_values(0):
                    continue
                sub = raw[sym]
                if "Close" not in sub:
                    continue
                frames.append(pd.DataFrame({
                    "symbol": sym, "date": sub.index.date,
                    "close": sub["Close"].to_numpy(float)}))
        else:
            frames.append(pd.DataFrame({
                "symbol": chunk[0], "date": raw.index.date,
                "close": raw["Close"].to_numpy(float)}))
    df = (pd.concat(frames, ignore_index=True)
          .dropna(subset=["close"]))
    df = df[df["close"] > 0]
    df.to_parquet(cache, index=False)
    return df
