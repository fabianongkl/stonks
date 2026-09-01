"""Build the scannable universe of US-listed common stocks.

Sources (both free, no key):
  * Nasdaq Trader symbol directory — every security listed on Nasdaq, NYSE,
    NYSE American, NYSE Arca, BATS, IEX.  Flags ETFs and test issues.
      http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
      http://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
  * SEC company_tickers.json — maps tickers to CIK numbers, which we need to
    pull fundamentals from EDGAR.
      https://www.sec.gov/files/company_tickers.json

A stock enters the universe if it is exchange-listed, is not an ETF/test
issue/derivative security (warrant, right, unit, preferred), and has a CIK
(i.e. it actually files financial statements with the SEC).
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"

# Security-name patterns that indicate a non-common-stock instrument.
# (A '%' in the name means a stated coupon — a note/preferred, not equity.)
_EXCLUDE_NAME = re.compile(
    r"%|\b(?:warrants?|rights?|units?|preferred|preference|depositary|"
    r"notes?|debentures?|bonds?|fund|closed.end|beneficial\s+interest|ETN)\b",
    re.IGNORECASE,
)


def _fetch_text(url: str) -> str:
    r = requests.get(url, timeout=60, headers={"User-Agent": config.SEC_USER_AGENT})
    r.raise_for_status()
    return r.text


def _load_exchange_listings() -> pd.DataFrame:
    """All exchange-listed symbols with an is_etf flag, from Nasdaq Trader."""
    frames = []

    txt = _fetch_text(NASDAQ_LISTED)
    df = pd.read_csv(io.StringIO(txt), sep="|")
    df = df[df["Symbol"].notna() & ~df["Symbol"].astype(str).str.startswith("File Creation")]
    frames.append(pd.DataFrame({
        "symbol": df["Symbol"].astype(str),
        "name": df["Security Name"].astype(str),
        "etf": df["ETF"].astype(str).str.upper().eq("Y"),
        "test": df["Test Issue"].astype(str).str.upper().eq("Y"),
        "exchange": "NASDAQ",
    }))

    txt = _fetch_text(OTHER_LISTED)
    df = pd.read_csv(io.StringIO(txt), sep="|")
    df = df[df["ACT Symbol"].notna() & ~df["ACT Symbol"].astype(str).str.startswith("File Creation")]
    frames.append(pd.DataFrame({
        "symbol": df["ACT Symbol"].astype(str),
        "name": df["Security Name"].astype(str),
        "etf": df["ETF"].astype(str).str.upper().eq("Y"),
        "test": df["Test Issue"].astype(str).str.upper().eq("Y"),
        "exchange": df["Exchange"].astype(str),
    }))

    out = pd.concat(frames, ignore_index=True)
    return out


def _load_sec_ciks() -> pd.DataFrame:
    """Ticker -> CIK map from the SEC."""
    r = requests.get(SEC_TICKERS, timeout=60,
                     headers={"User-Agent": config.SEC_USER_AGENT})
    r.raise_for_status()
    raw = r.json()
    rows = [(v["ticker"].upper(), int(v["cik_str"]), v["title"]) for v in raw.values()]
    return pd.DataFrame(rows, columns=["symbol_sec", "cik", "sec_title"])


def normalize_symbol(sym: str) -> str:
    """Canonical form: class shares joined with '-' (BRK.B / BRK/B -> BRK-B)."""
    return sym.strip().upper().replace(".", "-").replace("/", "-").replace("$", "-P")


def build_universe(use_cache: bool = True) -> pd.DataFrame:
    """Return DataFrame [symbol, name, exchange, cik] of scannable stocks.

    Cached per calendar day so repeat runs don't re-hit the sources.
    """
    cache = config.CACHE_DIR / f"universe_{date.today().isoformat()}.parquet"
    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    log.info("Building universe from Nasdaq Trader + SEC EDGAR ...")
    listings = _load_exchange_listings()
    listings = listings[~listings["etf"] & ~listings["test"]]
    listings = listings[~listings["name"].str.contains(_EXCLUDE_NAME, regex=True)]
    # symbols containing $ are preferred shares; . or / are usually fine (class shares)
    listings = listings[~listings["symbol"].str.contains(r"\$", regex=True)]
    listings["symbol"] = listings["symbol"].map(normalize_symbol)
    listings = listings.drop_duplicates("symbol")

    ciks = _load_sec_ciks()
    ciks["symbol"] = ciks["symbol_sec"].map(normalize_symbol)
    ciks = ciks.drop_duplicates("symbol")

    uni = listings.merge(ciks[["symbol", "cik"]], on="symbol", how="inner")
    uni = uni[["symbol", "name", "exchange", "cik"]].reset_index(drop=True)
    log.info("Universe: %d exchange-listed, SEC-filing common stocks", len(uni))

    uni.to_parquet(cache, index=False)
    return uni
