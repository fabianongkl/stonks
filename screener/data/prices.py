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


PANEL_COLS = ["symbol", "date", "close", "volume", "close_split",
              "dividend", "split"]


def fetch_prices(symbols: list[str], use_cache: bool = True) -> pd.DataFrame:
    """Return a long DataFrame covering ~PRICE_LOOKBACK_DAYS calendar days:

    close        split- AND dividend-adjusted close (total-return series; what
                 momentum and volatility are computed from, as before)
    close_split  split-adjusted close WITHOUT dividend adjustment (needed to
                 turn a cash dividend into a yield)
    dividend     cash dividend per share going ex on that date (0 if none),
                 in the current share terms Yahoo reports
    split        split ratio taking effect on that date (0 if none; 2.0 is a
                 2-for-1, 0.1 a 1-for-10 reverse split)

    Dividends and splits come back in the same bulk request (actions=True), so
    total-return accounting costs no extra calls (2026-11 review, proposal A).
    """
    cache = config.CACHE_DIR / f"prices_{date.today().isoformat()}.parquet"
    if use_cache and cache.exists():
        df = pd.read_parquet(cache)
        have = set(df["symbol"].unique())
        missing = [s for s in symbols if s not in have]
        # Cache may have been built from a sample run; top up if needed.
        df = _ensure_cols(df)
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
    import time

    start = date.today() - timedelta(days=config.PRICE_LOOKBACK_DAYS + 40)
    chunks = [symbols[i:i + config.YF_BATCH_SIZE]
              for i in range(0, len(symbols), config.YF_BATCH_SIZE)]
    out = []
    for i, chunk in enumerate(chunks, 1):
        log.info("Downloading prices: batch %d/%d (%d tickers)", i, len(chunks), len(chunk))
        # Yahoo throttles datacenter IPs (GitHub runners) hard: a throttled
        # batch comes back mostly empty rather than erroring.  Retry with
        # backoff until the batch returns a sane fraction of its tickers.
        part = None
        for attempt in range(1, config.YF_BATCH_RETRIES + 1):
            try:
                raw = yf.download(
                    tickers=chunk,
                    start=start.isoformat(),
                    auto_adjust=False,
                    actions=True,
                    progress=False,
                    group_by="ticker",
                    threads=True,
                )
            except Exception as e:
                log.warning("Batch %d attempt %d failed (%s)", i, attempt, e)
                raw = None
            part = _to_long(raw, chunk) if raw is not None and not raw.empty else None
            got = part["symbol"].nunique() if part is not None else 0
            if got >= 0.5 * len(chunk):
                break
            if attempt < config.YF_BATCH_RETRIES:
                wait = 20 * attempt
                log.warning("Batch %d returned %d/%d tickers — throttled? "
                            "retrying in %ds", i, got, len(chunk), wait)
                time.sleep(wait)
        if part is not None and not part.empty:
            out.append(part)
        time.sleep(1.0)   # gentle pacing between batches
    if not out:
        return pd.DataFrame(columns=PANEL_COLS)
    df = pd.concat(out, ignore_index=True)
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    return df


def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Older cached panels predate the corporate-action columns."""
    if "close_split" not in df.columns:
        df["close_split"] = np.nan
    for c in ("dividend", "split"):
        if c not in df.columns:
            df[c] = 0.0
    return df


def _frame_for(sym: str, sub: pd.DataFrame) -> pd.DataFrame | None:
    # auto_adjust=False returns "Close" (split-adjusted only) and "Adj Close"
    # (split- and dividend-adjusted).  "close" stays the adjusted series so
    # every price-derived metric is computed exactly as before.
    adj = "Adj Close" if "Adj Close" in sub.columns else "Close"
    if adj not in sub.columns:
        return None
    nan = pd.Series(np.nan, index=sub.index)
    zero = pd.Series(0.0, index=sub.index)
    return pd.DataFrame({
        "symbol": sym,
        "date": sub.index.date,
        "close": sub[adj].to_numpy(dtype=float),
        "volume": sub.get("Volume", nan).to_numpy(dtype=float),
        "close_split": sub.get("Close", nan).to_numpy(dtype=float),
        "dividend": sub.get("Dividends", zero).fillna(0.0).to_numpy(dtype=float),
        "split": sub.get("Stock Splits", zero).fillna(0.0).to_numpy(dtype=float),
    })


def _to_long(raw: pd.DataFrame, chunk: list[str]) -> pd.DataFrame:
    rows = []
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in chunk:
            if sym not in raw.columns.get_level_values(0):
                continue
            f = _frame_for(sym, raw[sym])
            if f is not None:
                rows.append(f)
    else:  # single-ticker chunk comes back flat
        f = _frame_for(chunk[0], raw)
        if f is not None:
            rows.append(f)
    if not rows:
        return pd.DataFrame(columns=PANEL_COLS)
    return pd.concat(rows, ignore_index=True)


def corporate_actions(px: pd.DataFrame) -> pd.DataFrame:
    """Dividend and split events in a price panel, ready for the permanent
    corporate_actions table.

    Columns: symbol, ex_date, kind ('DIV' | 'SPLIT'), amount, factor.

    DIV   amount = cash per share in the share terms in force ON THE EX-DATE
                   (Yahoo restates old dividends after every later split; we
                   undo that so a stored amount never changes meaning)
          factor = total-return multiplier 1 / (1 - dividend / prior close),
                   the same convention as Yahoo's adjusted close
    SPLIT amount = factor = the split ratio
    """
    px = _ensure_cols(px.copy())
    px = px.sort_values(["symbol", "date"])
    ev = px[(px["dividend"] > 0) | (px["split"] > 0)]
    if ev.empty:
        return pd.DataFrame(columns=["symbol", "ex_date", "kind", "amount", "factor"])
    prev_close = px.groupby("symbol")["close_split"].shift(1)
    rows = []
    for sym, g in ev.groupby("symbol"):
        splits = g[g["split"] > 0][["date", "split"]]
        for _, r in g.iterrows():
            if r["split"] > 0 and r["split"] != 1.0:
                rows.append((sym, str(r["date"]), "SPLIT",
                             float(r["split"]), float(r["split"])))
            if r["dividend"] > 0:
                later = splits[splits["date"] > r["date"]]["split"].prod()
                amount = float(r["dividend"]) * float(later)
                p0 = prev_close.loc[r.name]
                y = float(r["dividend"]) / p0 if p0 and p0 == p0 and p0 > 0 else None
                # a "yield" >= 100% means a bad prior close, not a dividend
                factor = 1.0 / (1.0 - y) if y is not None and 0 < y < 0.95 else None
                rows.append((sym, str(r["date"]), "DIV", amount, factor))
    return pd.DataFrame(rows, columns=["symbol", "ex_date", "kind", "amount", "factor"])
