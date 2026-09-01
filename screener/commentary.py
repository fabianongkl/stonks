"""Auto-generated daily commentary — written by the pipeline, not by an AI.

Every sentence is derived from the scan data by explicit rules (leaders,
factor drivers, rank moves versus the previous scan, concentration, cautions),
so the text is reproducible from the database and never claims anything the
numbers don't show.  The dashboard labels it as machine-generated.
"""

from __future__ import annotations

import pandas as pd

from .factors import FACTOR_NAMES

FACTOR_LABEL = {
    "value": "value (cheapness vs fundamentals)",
    "quality": "quality (profitability and balance-sheet strength)",
    "momentum": "momentum (12-month price trend)",
    "low_vol": "low volatility (price steadiness)",
}
FACTOR_SHORT = {"value": "value", "quality": "quality",
                "momentum": "momentum", "low_vol": "low-volatility"}


def full_coverage(scores: pd.DataFrame) -> pd.DataFrame:
    full = scores[scores["n_factors"] == 4].copy()
    full = full.sort_values("composite", ascending=False).reset_index(drop=True)
    full["fc_rank"] = full.index + 1
    return full


def _join(names: list[str]) -> str:
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _dominant_factors(df: pd.DataFrame, n: int = 2) -> list[str]:
    means = {f: df[f"{f}_score"].mean() for f in FACTOR_NAMES}
    ranked = sorted(means, key=means.get, reverse=True)
    return [f for f in ranked[:n] if means[f] > 0.15]


def overall(full: pd.DataFrame, prev_full: pd.DataFrame | None,
            scan_date: str, n_scored: int) -> str:
    top = full.head(30)
    s = []
    s.append(f"Scan of {scan_date}: {n_scored:,} stocks scored, "
             f"{len(full):,} with complete data on all four factors.")

    counts = top["sector"].value_counts()
    if len(counts) >= 6:
        s.append(f"Leadership is broad — the top 30 spans {len(counts)} sectors, "
                 f"led by {_join(list(counts.index[:2]))}.")
    else:
        s.append(f"Leadership is concentrated: {_join(list(counts.index[:2]))} "
                 f"dominate the top 30.")

    doms = _dominant_factors(top)
    if doms:
        s.append("Today's leaders score strongest on "
                 + _join([FACTOR_LABEL[f] for f in doms]) + ".")

    lead = top.iloc[0]
    s.append(f"The single highest composite belongs to {lead['symbol']} "
             f"({(lead['sector'] or 'Other')}), scoring {lead['composite']:+.2f}.")

    if prev_full is not None and not prev_full.empty:
        prev_top = set(prev_full.head(25)["symbol"])
        entrants = [r["symbol"] for _, r in top.head(25).iterrows()
                    if r["symbol"] not in prev_top]
        if entrants:
            s.append(f"New to the top 25 since the previous scan: "
                     f"{_join(entrants[:5])}.")
        else:
            s.append("The top 25 is unchanged from the previous scan.")
        prev_rank = dict(zip(prev_full["symbol"], prev_full["fc_rank"]))
        movers = []
        for _, r in top.head(25).iterrows():
            pr = prev_rank.get(r["symbol"])
            if pr and pr - r["fc_rank"] >= 100:
                movers.append(f"{r['symbol']} (#{int(pr)}→#{int(r['fc_rank'])})")
        if movers:
            s.append("Biggest climbers among the leaders: "
                     + _join(movers[:3]) + ".")
    return " ".join(s)


def sector(full: pd.DataFrame, prev_full: pd.DataFrame | None,
           name: str) -> str:
    sec = full[full["sector"] == name]
    if len(sec) < 5:
        return (f"{name}: only {len(sec)} stocks with complete data — too few "
                f"for a meaningful sector read.")
    top = sec.head(10)
    s = [f"{name}: {len(sec)} stocks with complete factor data."]

    leaders = [f"{r['symbol']} (#{int(r['fc_rank'])} overall)"
               for _, r in top.head(3).iterrows()]
    s.append(f"Sector leaders: {_join(leaders)}.")

    doms = _dominant_factors(top)
    if doms:
        s.append(f"The sector's strongest names lean on "
                 + _join([FACTOR_SHORT[f] for f in doms]) + ".")

    mom = top["mom_12_1"].median()
    if pd.notna(mom):
        trend = ("in strong uptrends" if mom > 0.25 else
                 "trending up" if mom > 0.05 else
                 "roughly flat over the past year" if mom > -0.05 else
                 "in downtrends")
        s.append(f"Its leaders are {trend} "
                 f"(median 12-month return {mom * 100:+.0f}%).")

    lev = top["leverage"].median()
    if pd.notna(lev) and lev > 0.75:
        s.append("Caution: leverage runs high among these names "
                 f"(median liabilities at {lev * 100:.0f}% of assets).")

    if prev_full is not None and not prev_full.empty:
        prev_sec_top = set(prev_full[prev_full["sector"] == name]
                           .head(10)["symbol"])
        fresh = [sym for sym in top["symbol"] if sym not in prev_sec_top]
        if fresh and prev_sec_top:
            s.append(f"New in the sector's top ten: {_join(fresh[:4])}.")
    return " ".join(s)
