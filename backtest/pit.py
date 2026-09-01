"""Point-in-time fundamental lookups.

Converts the raw fact table (cik, field, end, filed, dur_days, val) into
"as-filed timelines": for each company and field, a time series of what the
best-known value WAS at each filing moment.  A backtest date then sees only
the last timeline point filed on or before it — never the future.

Value construction mirrors the live screener:
  * income-statement flows: TTM = sum of the last 4 distinct quarterly
    (~90-day) facts when available, else the latest annual;
  * cash-flow items: latest annual (year-to-date quarterly filings make
    quarterly reconstruction unreliable — same limitation as live);
  * balance-sheet instants and shares: latest end known at filing time,
    plus the value from ~one year earlier (for issuance / asset growth).
Restatements are handled naturally: a later filing overwrites the value for
the same period END from that filing date forward.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("bt.pit")

TTM_FIELDS = {"revenue", "net_income", "op_income", "gross_profit"}
ANNUAL_FIELDS = {"cfo", "capex"}
INSTANT_FIELDS = {"assets", "liabilities", "equity", "cash", "shares"}
# fields whose timeline also carries a ~1-year-earlier value
PRIOR_FIELDS = {"revenue", "assets", "shares"}


def _is_quarter(d): return 80 <= d <= 100
def _is_annual(d): return 330 <= d <= 400


def _flow_timeline(g: pd.DataFrame, field: str) -> list[tuple]:
    """[(filed, val, prior)] for one company's flow field."""
    quarters: dict[str, float] = {}
    annuals: dict[str, float] = {}
    out = []
    for filed, end, dur, val in g[["filed", "end", "dur_days", "val"]].itertuples(index=False):
        if _is_quarter(dur) and field in TTM_FIELDS:
            quarters[end] = val
        elif _is_annual(dur):
            annuals[end] = val
        else:
            continue
        # current best TTM estimate as of this filing
        cur = np.nan
        if field in TTM_FIELDS and len(quarters) >= 4:
            ends = sorted(quarters)[-4:]
            span = (pd.Timestamp(ends[-1]) - pd.Timestamp(ends[0])).days
            if span <= 300:
                cur = sum(quarters[e] for e in ends)
        if np.isnan(cur) and annuals:
            # annual + post-year-end quarters − the same quarters a year
            # earlier (mirrors the live screener's TTM splice)
            a_end = sorted(annuals)[-1]
            cur = annuals[a_end]
            if field in TTM_FIELDS:
                for qe in sorted(quarters):
                    if qe <= a_end:
                        continue
                    target = pd.Timestamp(qe) - pd.Timedelta(days=365)
                    prior_q = None
                    for e in quarters:
                        if e <= a_end and abs((pd.Timestamp(e) - target).days) <= 20:
                            prior_q = e
                            break
                    if prior_q:
                        cur += quarters[qe] - quarters[prior_q]
        prior = np.nan
        if field in PRIOR_FIELDS and len(annuals) >= 2:
            prior = annuals[sorted(annuals)[-2]]
        if not np.isnan(cur):
            out.append((filed, cur, prior))
    return out


def _instant_timeline(g: pd.DataFrame) -> list[tuple]:
    """[(filed, val, prior)] for one company's instantaneous field."""
    seen: dict[str, float] = {}
    out = []
    for filed, end, dur, val in g[["filed", "end", "dur_days", "val"]].itertuples(index=False):
        # shares includes annual weighted-average (duration) facts — treat by end
        seen[end] = val
        latest = sorted(seen)[-1]
        d1 = pd.Timestamp(latest)
        prior = np.nan
        for e in sorted(seen, reverse=True):
            dd = (d1 - pd.Timestamp(e)).days
            if 300 <= dd <= 470:
                prior = seen[e]
                break
        out.append((filed, seen[latest], prior))
    return out


def build_timelines(facts: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """field -> DataFrame(cik, filed, val, prior) sorted by filed."""
    facts = facts[facts["field"] != "_none"]
    facts = facts.drop_duplicates(["cik", "field", "end", "dur_days", "filed"])
    facts = facts.sort_values(["cik", "filed", "end"])
    out: dict[str, pd.DataFrame] = {}
    for field, fg in facts.groupby("field"):
        rows = []
        builder = _instant_timeline if field in INSTANT_FIELDS else \
            (lambda g, f=field: _flow_timeline(g, f))
        for cik, g in fg.groupby("cik"):
            for filed, val, prior in builder(g):
                rows.append((cik, filed, val, prior))
        df = pd.DataFrame(rows, columns=["cik", "filed", "val", "prior"])
        df["filed"] = pd.to_datetime(df["filed"])
        # keep the LAST emission per (cik, filed) — the best estimate that day
        df = df.sort_values(["cik", "filed"]).drop_duplicates(
            ["cik", "filed"], keep="last")
        out[field] = df.sort_values("filed").reset_index(drop=True)
        log.info("timeline %-12s: %d points, %d companies",
                 field, len(df), df["cik"].nunique())
    return out


def asof(timelines: dict[str, pd.DataFrame], field: str,
         ciks: pd.Series, date: pd.Timestamp,
         max_staleness_days: int = 550) -> pd.DataFrame:
    """As-of values: DataFrame indexed like `ciks` with columns [val, prior].

    Facts older than max_staleness_days are treated as missing — a company
    that stopped filing years ago shouldn't keep stale 'fundamentals'.
    """
    tl = timelines.get(field)
    if tl is None or tl.empty:
        return pd.DataFrame({"val": np.nan, "prior": np.nan}, index=ciks.index)
    left = pd.DataFrame({"cik": ciks.to_numpy(), "filed": date}).sort_values("cik")
    left["filed"] = pd.to_datetime(left["filed"])
    m = pd.merge_asof(left.sort_values("filed"), tl, on="filed", by="cik",
                      direction="backward",
                      tolerance=pd.Timedelta(days=max_staleness_days))
    m.index = ciks.index[np.argsort(ciks.to_numpy(), kind="stable")]
    return m[["val", "prior"]].reindex(ciks.index)
