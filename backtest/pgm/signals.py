"""Point-in-time fundamental and price signals for the PGM screen.

Rules: docs/PGM_SCREEN.md sections 2, 4, 5, 6.

Fundamentals become a (cik, quarter) panel where every derived number carries
`k_<name>`, the date it first became public (max EDGAR `filed` date of its
inputs). The engine only reads a value at rebalance t if k <= t - 1 day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

EPOCH = pd.Timestamp("1970-01-01")
DUR_BUCKETS = [(1, 80, 100), (2, 170, 200), (3, 260, 290), (4, 350, 380)]


# --------------------------------------------------------------------------
# fundamentals
# --------------------------------------------------------------------------
def _qid(end: pd.Series) -> pd.Series:
    """Nearest calendar quarter end -> integer id (year*4 + q-1)."""
    e = end + pd.Timedelta(days=45)            # shift so nearest q-end rounds down
    q = (e.dt.month - 1) // 3                   # 0..3 = quarter containing e
    qid = e.dt.year * 4 + q - 1                 # the quarter that ENDED nearest
    return qid


def qid_end(qid: pd.Series | np.ndarray) -> pd.Series:
    qid = pd.Series(qid)
    y, q = qid // 4, qid % 4
    return pd.to_datetime(pd.DataFrame({"year": y, "month": 3 * (q + 1), "day": 1})) \
        + pd.offsets.MonthEnd(0)


def first_filed(facts: pd.DataFrame) -> pd.DataFrame:
    """One value per (cik, field, end, dur): the earliest filed (ties -> tag priority)."""
    f = facts[facts["field"] != "_none"].copy()
    f["end"] = pd.to_datetime(f["end"])
    f["filed"] = pd.to_datetime(f["filed"])
    f = f.sort_values(["filed", "prio"])
    return f.drop_duplicates(["cik", "field", "end", "dur"], keep="first")


def _quarters(ff: pd.DataFrame, field: str) -> pd.DataFrame:
    """Discrete quarterly values for a flow field: [cik, qid, v, k]."""
    f = ff[ff["field"] == field]
    parts = []
    by_k = {}
    for k, lo, hi in DUR_BUCKETS:
        b = f[(f["dur"] >= lo) & (f["dur"] <= hi)][["cik", "end", "filed", "val"]]
        b = b.sort_values("filed").drop_duplicates(["cik", "end"])
        by_k[k] = b
    parts.append(by_k[1].rename(columns={"val": "v", "filed": "k"}))
    for k in (2, 3, 4):
        cur, prev = by_k[k].copy(), by_k[k - 1].copy()
        if cur.empty or prev.empty:
            continue
        cur["key"] = cur["end"] - pd.Timedelta(days=91)
        prev = prev.rename(columns={"end": "pend", "filed": "pfiled", "val": "pval"})
        cur = cur.sort_values("key")
        prev = prev.sort_values("pend")
        m = pd.merge_asof(cur, prev, left_on="key", right_on="pend", by="cik",
                          tolerance=pd.Timedelta(days=15), direction="nearest")
        m = m.dropna(subset=["pval"])
        m["v"] = m["val"] - m["pval"]
        m["k"] = m[["filed", "pfiled"]].max(axis=1)
        parts.append(m[["cik", "end", "k", "v"]])
    q = pd.concat(parts, ignore_index=True)
    q["qid"] = _qid(q["end"])
    q = q.sort_values("k").drop_duplicates(["cik", "qid"], keep="first")
    return q[["cik", "qid", "v", "k"]]


def _instant_q(ff: pd.DataFrame, field: str) -> pd.DataFrame:
    f = ff[(ff["field"] == field) & (ff["dur"] == -1)][["cik", "end", "filed", "val"]].copy()
    f["qid"] = _qid(f["end"])
    f = f.sort_values("filed").drop_duplicates(["cik", "qid"])
    return f.rename(columns={"val": "v", "filed": "k"})[["cik", "qid", "v", "k"]]


def _issuance(facts: pd.DataFrame) -> pd.DataFrame:
    """Same-filing YoY change in weighted-average basic shares: [cik, qid, v, k]."""
    f = facts[(facts["field"] == "waso") & facts["form"].isin(
        ["10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"])].copy()
    f["end"] = pd.to_datetime(f["end"])
    f["filed"] = pd.to_datetime(f["filed"])
    f["b"] = 0
    for k, lo, hi in DUR_BUCKETS:
        f.loc[(f["dur"] >= lo) & (f["dur"] <= hi), "b"] = k
    f = f[f["b"] > 0]
    # current period of each filing = max end in that filing
    f["maxend"] = f.groupby(["cik", "filed"])["end"].transform("max")
    cur = f[f["end"] == f["maxend"]].sort_values("b").drop_duplicates(["cik", "filed"])
    prev = f[f["end"] < f["maxend"]][["cik", "filed", "b", "end", "val"]]
    m = cur.merge(prev, on=["cik", "filed", "b"], suffixes=("", "_p"))
    gap = (m["end"] - m["end_p"]).dt.days
    m = m[(gap >= 350) & (gap <= 380) & (m["val_p"] > 0) & (m["val"] > 0)]
    m["v"] = m["val"] / m["val_p"] - 1
    m["qid"] = _qid(m["end"])
    m = m.sort_values("filed").drop_duplicates(["cik", "qid"])
    return m.rename(columns={"filed": "k"})[["cik", "qid", "v", "k"]]


def fundamental_panel(facts: pd.DataFrame) -> pd.DataFrame:
    """(cik, qid) panel of point-in-time fundamental signals with known dates."""
    ff = first_filed(facts)
    q = {name: _quarters(ff, name) for name in
         ("revenue", "gross_profit", "cogs", "op_income", "cfo")}
    # gross profit fallback: revenue - cogs, known when both are
    rc = q["revenue"].merge(q["cogs"], on=["cik", "qid"], suffixes=("_r", "_c"))
    rc = pd.DataFrame({"cik": rc["cik"], "qid": rc["qid"], "v": rc["v_r"] - rc["v_c"],
                       "k": rc[["k_r", "k_c"]].max(axis=1)})
    gp = pd.concat([q["gross_profit"], rc]).sort_values("k") \
        .drop_duplicates(["cik", "qid"]).reset_index(drop=True)
    series = {"rev": q["revenue"], "gp": gp, "opi": q["op_income"], "cfo": q["cfo"],
              "assets": _instant_q(ff, "assets"), "iss": _issuance(facts)}

    # full (cik, qid) grid so shift(n) == n quarters back
    keys = pd.concat([s[["cik", "qid"]] for s in series.values()]).drop_duplicates()
    rng = keys.groupby("cik")["qid"].agg(["min", "max"])
    grid = pd.DataFrame({
        "cik": np.repeat(rng.index.values, (rng["max"] - rng["min"] + 1).values),
        "qid": np.concatenate([np.arange(a, b + 1) for a, b in zip(rng["min"], rng["max"])]),
    })
    p = grid
    for name, s in series.items():
        p = p.merge(s.rename(columns={"v": name, "k": f"k_{name}"}),
                    on=["cik", "qid"], how="left")
    p = p.sort_values(["cik", "qid"]).reset_index(drop=True)
    kcols = [c for c in p.columns if c.startswith("k_")]
    for c in kcols:                       # dates -> float days for rolling max
        p[c] = (p[c] - EPOCH).dt.days.astype(float)
    g = p.groupby("cik", sort=False)

    def roll(col: str, n: int, fn: str):
        return g[col].transform(lambda s: getattr(s.rolling(n, min_periods=n), fn)())

    def sh(s: pd.Series, n: int) -> pd.Series:
        return s.groupby(p["cik"]).shift(n)

    def kmax(*ks):
        return pd.concat(ks, axis=1).max(axis=1, skipna=False)

    for c in ("rev", "gp", "opi", "cfo"):
        p[f"{c}_ttm"] = roll(c, 4, "sum")
        p[f"k_{c}_ttm"] = roll(f"k_{c}", 4, "max").where(p[f"{c}_ttm"].notna())

    # assets: latest instant at this quarter (carry forward up to 1 quarter)
    p["assets_l"] = p["assets"].fillna(sh(p["assets"], 1))
    p["k_assets_l"] = p["k_assets"].fillna(sh(p["k_assets"], 1))

    # profitability
    p["gpa"] = p["gp_ttm"] / p["assets_l"].where(p["assets_l"] > 0)
    p["k_gpa"] = kmax(p["k_gp_ttm"], p["k_assets_l"])
    p["d_ocf"] = (p["cfo_ttm"] - sh(p["cfo_ttm"], 4)) / p["assets_l"].where(p["assets_l"] > 0)
    p["k_d_ocf"] = kmax(p["k_cfo_ttm"], sh(p["k_cfo_ttm"], 4), p["k_assets_l"])

    # growth
    prev_rev = sh(p["rev_ttm"], 4)
    p["rev_g"] = p["rev_ttm"] / prev_rev.where(prev_rev > 0) - 1
    p["k_rev_g"] = kmax(p["k_rev_ttm"], sh(p["k_rev_ttm"], 4))
    p["rev_accel"] = p["rev_g"] - sh(p["rev_g"], 1)
    p["k_rev_accel"] = kmax(p["k_rev_g"], sh(p["k_rev_g"], 1))
    rv = p["rev_ttm"].where(p["rev_ttm"] > 0)
    om, gm = p["opi_ttm"] / rv, p["gp_ttm"] / rv
    om_exp, gm_exp = om - sh(om, 4), gm - sh(gm, 4)
    k_om = kmax(p["k_opi_ttm"], p["k_rev_ttm"], sh(p["k_opi_ttm"], 4), sh(p["k_rev_ttm"], 4))
    k_gm = kmax(p["k_gp_ttm"], p["k_rev_ttm"], sh(p["k_gp_ttm"], 4), sh(p["k_rev_ttm"], 4))
    p["margin_exp"] = om_exp.where(om_exp.notna(), gm_exp)
    p["k_margin_exp"] = k_om.where(om_exp.notna(), k_gm)

    # SUE proxy: YoY change in quarterly operating income / std of prior 8 changes
    d = p["opi"] - sh(p["opi"], 4)
    p["_d"] = d
    sd = sh(p.groupby("cik")["_d"].transform(
        lambda s: s.rolling(8, min_periods=4).std()), 1)
    p["sue"] = d / sd.where(sd > 0)
    # earlier quarters in the std window were filed before quarter E itself
    p["k_sue"] = kmax(p["k_opi"], sh(p["k_opi"], 4)).where(p["sue"].notna())

    # issuance (lower is better -> signal is the negative)
    p["low_issue"] = -p["iss"]
    p["k_low_issue"] = p["k_iss"]

    p["qend"] = qid_end(p["qid"].values).values
    keep = ["cik", "qid", "qend", "rev_ttm", "k_rev_ttm", "cfo_ttm", "k_cfo_ttm",
            "gpa", "k_gpa", "d_ocf", "k_d_ocf", "rev_g", "k_rev_g", "rev_accel",
            "k_rev_accel", "margin_exp", "k_margin_exp", "sue", "k_sue",
            "low_issue", "k_low_issue"]
    out = p[keep].copy()
    out["k_core"] = kmax(out["k_rev_ttm"], out["k_cfo_ttm"])
    out = out[out["k_core"].notna()].reset_index(drop=True)
    for c in [c for c in out.columns if c.startswith("k_")]:
        out[c] = EPOCH + pd.to_timedelta(out[c], unit="D")
    return out


def shares_table(facts: pd.DataFrame) -> pd.DataFrame:
    """Shares outstanding facts by filed date: [cik, k, shares] (cover page first)."""
    f = facts[facts["field"] == "shares_out"].copy()
    f["k"] = pd.to_datetime(f["filed"])
    f["end"] = pd.to_datetime(f["end"])
    f = f[f["val"] > 0].sort_values(["cik", "k", "prio", "end"],
                                    ascending=[True, True, True, False])
    f = f.drop_duplicates(["cik", "k"])
    return f[["cik", "k", "val"]].rename(columns={"val": "shares"})


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------
def price_panels(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Wide date x symbol panels: adj, close (split-adj), raw (unadjusted), dvol."""
    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"])
    px = px.drop_duplicates(["symbol", "date"])
    adj = px.pivot(index="date", columns="symbol", values="adj").sort_index()
    close = px.pivot(index="date", columns="symbol", values="close").reindex(adj.index)
    vol = px.pivot(index="date", columns="symbol", values="volume").reindex(adj.index)
    spl = px.pivot(index="date", columns="symbol", values="split").reindex(adj.index)
    spl = spl.fillna(0.0).replace(0.0, 1.0)
    # raw(d) = close(d) * product of split ratios strictly after d
    after = spl.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)
    raw = close * after
    dvol = close * vol
    return {"adj": adj, "close": close, "raw": raw, "dvol": dvol}
