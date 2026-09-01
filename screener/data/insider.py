"""Insider transactions from the SEC's Form 3/4/5 structured data sets.

The SEC publishes every insider filing as quarterly bulk TSV archives:

    https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip

We aggregate, per issuer, the net dollar value of open-market purchases (code
P) minus open-market sales (code S) over the most recent available quarters.
Corporate insiders buying their own stock with their own money is one of the
oldest documented positive signals (Seyhun 1986, 1998; Lakonishok & Lee 2001);
routine selling is far less informative (diversification, taxes), which is why
purchases and sales are both included but the metric is net *dollars*, where
a cluster of genuine buys stands out against the constant drizzle of sales.

The data set lags by up to a quarter — a known, documented limitation; the
signal's evidence base is at multi-month horizons, so a stale quarter is
acceptable.  If the download fails, the metric is skipped gracefully (NaN)
and the factor model simply runs without it.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date

import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

URL = ("https://www.sec.gov/files/structureddata/data/"
       "insider-transactions-data-sets/{y}q{q}_form345.zip")
N_QUARTERS = 2      # aggregate over the latest 2 available quarters


def _recent_quarter_labels(n: int = 6) -> list[tuple[int, int]]:
    y, q = date.today().year, (date.today().month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append((y, q))
    return out


def _download_quarter(y: int, q: int) -> bytes | None:
    cache = config.CACHE_DIR / f"form345_{y}q{q}.zip"
    if cache.exists():
        return cache.read_bytes()
    try:
        r = requests.get(URL.format(y=y, q=q), timeout=300,
                         headers={"User-Agent": config.SEC_USER_AGENT})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        cache.write_bytes(r.content)
        return r.content
    except Exception as e:
        log.warning("Insider data set %dq%d fetch failed: %s", y, q, e)
        return None


def _find_col(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        cl = c.upper()
        if all(n in cl for n in needles):
            return c
    return None


def _parse_quarter(blob: bytes) -> pd.Series | None:
    """cik -> net insider dollars (buys − sells) for one quarterly archive."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = {n.upper(): n for n in zf.namelist()}
        sub_name = next((names[n] for n in names if "SUBMISSION" in n or n == "SUB.TSV"), None)
        tr_name = next((names[n] for n in names if "NONDERIV_TRANS" in n), None)
        if not sub_name or not tr_name:
            log.warning("Insider archive missing expected tables: %s", list(names)[:8])
            return None
        sub = pd.read_csv(zf.open(sub_name), sep="\t", low_memory=False)
        tr = pd.read_csv(zf.open(tr_name), sep="\t", low_memory=False)

        acc_s = _find_col(list(sub.columns), "ACCESSION")
        cik_c = _find_col(list(sub.columns), "ISSUERCIK") or _find_col(list(sub.columns), "CIK")
        acc_t = _find_col(list(tr.columns), "ACCESSION")
        code_c = _find_col(list(tr.columns), "TRANS", "CODE")
        sh_c = _find_col(list(tr.columns), "TRANS", "SHARES")
        px_c = _find_col(list(tr.columns), "PRICEPERSHARE") or _find_col(list(tr.columns), "TRANS", "PRICE")
        if not all([acc_s, cik_c, acc_t, code_c, sh_c, px_c]):
            log.warning("Insider archive columns unrecognised; skipping")
            return None

        tr = tr[tr[code_c].isin(["P", "S"])]
        tr = tr.merge(sub[[acc_s, cik_c]], left_on=acc_t, right_on=acc_s, how="inner")
        shares = pd.to_numeric(tr[sh_c], errors="coerce")
        price = pd.to_numeric(tr[px_c], errors="coerce")
        val = (shares * price).fillna(0)
        # Filings contain occasional garbage (mis-keyed units produce
        # quadrillion-dollar "purchases").  No real insider transaction is
        # priced above $50k/share or worth over $5B — cap, don't trust.
        val = val.where((price > 0.0) & (price < 5e4) & (val < 5e9), 0)
        signed = val.where(tr[code_c] == "P", -val)
        out = signed.groupby(pd.to_numeric(tr[cik_c], errors="coerce")).sum()
        out.index = out.index.astype("int64")
        return out
    except Exception as e:
        log.warning("Insider archive parse failed: %s", e)
        return None


def fetch_insider_net() -> pd.Series:
    """cik -> net insider open-market dollars over the latest available
    quarters.  Empty Series (not an error) when no data can be retrieved."""
    parts = []
    for y, q in _recent_quarter_labels():
        blob = _download_quarter(y, q)
        if blob is None:
            continue
        s = _parse_quarter(blob)
        if s is not None:
            parts.append(s)
            log.info("Insider data: %dq%d loaded (%d issuers with P/S activity)",
                     y, q, len(s))
        if len(parts) >= N_QUARTERS:
            break
    if not parts:
        log.warning("No insider transaction data available — metric skipped this run")
        return pd.Series(dtype=float)
    total = parts[0]
    for p in parts[1:]:
        total = total.add(p, fill_value=0)
    return total
