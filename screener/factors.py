"""Factor computation — the analytical heart of the screener.

Four factor families, each backed by decades of published research (full
citations and plain-English explanations in docs/METHODOLOGY.md):

  VALUE      Are you paying a low price for the business's earnings, cash
             flow and book value?  (Basu 1977; Fama & French 1992)
  QUALITY    Is the business profitable, efficient and conservatively
             financed, with honest earnings?  (Novy-Marx 2013; Sloan 1996;
             Piotroski 2000; Asness, Frazzini & Pedersen 2019)
  MOMENTUM   Has the stock been trending up over the past year (excluding
             the most recent month)?  (Jegadeesh & Titman 1993)
  LOW VOL    Has the stock been steady rather than wild?  (Ang et al. 2006;
             Baker, Bradley & Wurgler 2011)

Each raw metric is converted to a Gaussian rank z-score (rank percentile
mapped through the inverse normal CDF — robust to the fat tails financial
ratios always have).  A factor score is the average of its metric z-scores;
the composite is a weighted sum of factor scores with weights that the
learning module adapts over time.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)

TRADING_DAYS_YEAR = 252
MOM_SKIP = 21          # skip most recent month (short-term reversal effect)
MOM_LOOKBACK = 252     # 12 months
MOM6_LOOKBACK = 126
VOL_LOOKBACK = 252
DOLLAR_VOL_LOOKBACK = 63

FACTOR_NAMES = ["value", "quality", "momentum", "low_vol"]

# metric -> (factor family, direction: +1 higher-is-better / -1 lower-is-better)
METRIC_MAP = {
    "earnings_yield":      ("value", +1),
    "fcf_yield":           ("value", +1),
    "book_to_market":      ("value", +1),
    "issuance":            ("value", -1),   # net share issuance: dilution is anti-value
    "gross_profitability": ("quality", +1),
    "roa":                 ("quality", +1),
    "leverage":            ("quality", -1),
    "accruals":            ("quality", -1),
    "asset_growth":        ("quality", -1),  # empire-building destroys returns (CMA)
    "insider_net_mcap":    ("quality", +1),  # insiders buying their own stock
    "mom_12_1":            ("momentum", +1),
    "mom_6":               ("momentum", +1),
    "volatility":          ("low_vol", -1),
}


# ---------------------------------------------------------------------------
# Price-derived metrics
# ---------------------------------------------------------------------------

def price_metrics(prices: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol: last close, momentum, volatility, median dollar volume."""
    rows = []
    for sym, g in prices.sort_values("date").groupby("symbol"):
        c = g["close"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        n = len(c)
        if n < 40:          # too little history to say anything
            continue
        last = c[-1]
        rets = np.diff(np.log(c))

        def total_ret(lb, skip=0):
            if n < lb + skip + 1:
                return np.nan
            a, b = c[-(lb + skip + 1)], c[-(skip + 1)]
            return b / a - 1.0 if a > 0 else np.nan

        vol_window = rets[-VOL_LOOKBACK:]
        vol = float(np.std(vol_window, ddof=1) * np.sqrt(TRADING_DAYS_YEAR)) \
            if len(vol_window) >= 60 else np.nan

        dv = c[-DOLLAR_VOL_LOOKBACK:] * v[-DOLLAR_VOL_LOOKBACK:]
        dollar_vol = float(np.nanmedian(dv)) if len(dv) else np.nan

        rows.append({
            "symbol": sym,
            "last_close": last,
            "mom_12_1": total_ret(MOM_LOOKBACK, MOM_SKIP),
            "mom_6": total_ret(MOM6_LOOKBACK, MOM_SKIP),
            "volatility": vol,
            "dollar_volume": dollar_vol,
            "history_days": n,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Fundamental-derived metrics
# ---------------------------------------------------------------------------

def fundamental_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """df has fundamentals merged in, plus market_cap.  Adds ratio columns."""
    out = df.copy()

    # Sanity guard on market cap: multi-class filers sometimes report a single
    # class's share count under the common tags (Berkshire's B-share price ×
    # A-share count computes a "$0.5B" company).  No real company has revenue
    # 30× or book equity 15× its market cap — if the fundamentals scream that
    # loudly, the share count is wrong: treat market cap as missing rather
    # than hand the stock an absurd value score.
    mcap = out["market_cap"]
    bad = ((out["revenue"] > 30 * mcap) | (out["equity"] > 15 * mcap)).fillna(False)
    if bad.any():
        import logging
        logging.getLogger(__name__).info(
            "Market-cap sanity guard: %d stocks' share counts look wrong "
            "(single-class tag on multi-class filer) — value metrics dropped: %s",
            int(bad.sum()), ", ".join(out.loc[bad, "symbol"].head(8)))
    out.loc[bad, "market_cap"] = np.nan
    mcap = out["market_cap"]
    assets = out["assets"]

    with np.errstate(all="ignore"):
        out["earnings_yield"] = out["net_income"] / mcap
        fcf = out["cfo"] - out["capex"].fillna(0)
        out["fcf_yield"] = fcf / mcap
        out["book_to_market"] = out["equity"] / mcap
        out["gross_profitability"] = out["gross_profit"] / assets
        out["roa"] = out["net_income"] / assets
        out["leverage"] = out["liabilities"] / assets
        out["accruals"] = (out["net_income"] - out["cfo"]) / assets
        out["revenue_growth"] = out["revenue"] / out["revenue_prior"] - 1.0
        # Net share issuance (Pontiff & Woodgate 2008): change in split-adjusted
        # share count over ~1 year.  Buybacks -> negative, dilution -> positive.
        out["issuance"] = out["shares_out"] / out["shares_prior"] - 1.0
        # Asset growth (Cooper, Gulen & Schill 2008): balance-sheet expansion
        # rate; aggressive growers underperform disciplined ones.
        out["asset_growth"] = out["assets"] / out["assets_prior"] - 1.0
        # Insider net open-market dollars scaled by size (Seyhun; Lakonishok & Lee)
        if "insider_net" in out.columns:
            out["insider_net_mcap"] = out["insider_net"] / mcap
        else:
            out["insider_net_mcap"] = np.nan

    # Ratios on non-positive denominators are meaningless — null them.
    for col in ["earnings_yield", "fcf_yield", "book_to_market", "insider_net_mcap"]:
        out.loc[~(mcap > 0), col] = np.nan
    for col in ["gross_profitability", "roa", "leverage", "accruals"]:
        out.loc[~(assets > 0), col] = np.nan
    out.loc[~(out["shares_prior"] > 0), "issuance"] = np.nan
    out.loc[~(out["assets_prior"] > 0), "asset_growth"] = np.nan
    # Negative book value: not "cheap", it's broken equity — treat as missing.
    out.loc[out["book_to_market"] < 0, "book_to_market"] = np.nan
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _rank_z(s: pd.Series, groups: pd.Series | None = None) -> pd.Series:
    """Gaussian rank z-score: rank the metric cross-sectionally, map rank
    percentiles through the inverse normal CDF.

    Why ranks instead of raw z-scores: financial ratios and momentum have
    violently fat-tailed distributions, so a raw z-score lets one metric's
    outliers (a biotech up 400%) dominate the composite.  Rank-based scoring
    is the standard robust practice in quantitative equity (it is monotonic,
    outlier-proof, and makes every metric's score distribution identical, so
    factors are combined on equal footing).  The most extreme stock in a
    3,500-name universe lands near z = ±3.4 rather than an unbounded value.

    When `groups` (sectors) is given, ranking happens WITHIN each group of at
    least MIN_SECTOR_GROUP valid values, so "cheap for a bank" and "cheap for
    a software company" are measured against the right peers; stocks in
    groups too small to rank reliably fall back to the global ranking.
    """
    from statistics import NormalDist
    inv = NormalDist().inv_cdf

    def z_of(x: pd.Series) -> pd.Series:
        n = int(x.notna().sum())
        if n < 30:
            return pd.Series(np.nan, index=x.index)
        pct = (x.rank(method="average") - 0.5) / n
        return pct.map(lambda p: inv(p) if pd.notna(p) else np.nan)

    x = s.astype(float)
    out = z_of(x)                     # global ranking (also the fallback)
    if groups is not None:
        for _, idx in x.groupby(groups.fillna("Other")).groups.items():
            sub = x.loc[idx]
            if int(sub.notna().sum()) >= config.MIN_SECTOR_GROUP:
                out.loc[idx] = z_of(sub)
    return out


def score(df: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Add z_<metric>, <factor>_score, composite, rank columns.

    If a `sector` column is present (and SECTOR_NEUTRAL is on), every metric
    is ranked within sector — the composite then measures how good a stock is
    versus its own industry, not whether its industry flatters the metric.
    """
    out = df.copy()
    groups = (out["sector"] if config.SECTOR_NEUTRAL and "sector" in out.columns
              else None)

    factor_parts: dict[str, list[pd.Series]] = {f: [] for f in FACTOR_NAMES}
    for metric, (factor, direction) in METRIC_MAP.items():
        z = _rank_z(out[metric], groups) * direction
        out[f"z_{metric}"] = z
        factor_parts[factor].append(z)

    for factor, parts in factor_parts.items():
        out[f"{factor}_score"] = pd.concat(parts, axis=1).mean(axis=1)

    # Composite: weighted mean over AVAILABLE factors (weights renormalised),
    # so a missing factor is neutral rather than fatal — but we record coverage
    # and the dashboard's headline picks require full coverage.
    fs = out[[f"{f}_score" for f in FACTOR_NAMES]]
    w = pd.Series({f"{f}_score": weights.get(f, 0.0) for f in FACTOR_NAMES})
    avail = fs.notna()
    wsum = avail.mul(w, axis=1).sum(axis=1)
    comp = fs.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, np.nan)
    out["composite"] = comp
    out["n_factors"] = avail.sum(axis=1)

    out["rank"] = out["composite"].rank(ascending=False, method="min")
    return out.sort_values("rank")
