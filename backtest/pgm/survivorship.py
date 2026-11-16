"""Measure the survivorship gap: 2013 EDGAR revenue filers missing today.

    python -m backtest.pgm.survivorship

Uses the SEC frames API (calendar-year 2013 revenue, us-gaap), 2 requests.
Writes results/survivorship.json.
"""

from __future__ import annotations

import json

import pandas as pd

from screener.data import fundamentals as fnd
from . import config as C


def main() -> None:
    cl = fnd._SecClient()
    ciks: dict[int, float] = {}
    for tag in ("Revenues", "SalesRevenueNet"):
        j = cl.get_frame("us-gaap", tag, "USD", "CY2013") or {}
        for row in j.get("data", []):
            if row.get("val", 0) > 0:
                ciks[int(row["cik"])] = max(ciks.get(int(row["cik"]), 0), row["val"])
    uni = set(pd.read_parquet(C.UNIVERSE)["cik"].astype(int))
    s = pd.Series(ciks)
    big = s[s >= 100e6]
    out = {
        "cy2013_revenue_filers": int(len(s)),
        "of_which_in_universe_today": int(s.index.isin(uni).sum()),
        "cy2013_filers_revenue_ge_100m": int(len(big)),
        "of_which_in_universe_today_ge_100m": int(big.index.isin(uni).sum()),
    }
    out["missing_share"] = 1 - out["of_which_in_universe_today"] / out["cy2013_revenue_filers"]
    out["missing_share_ge_100m"] = 1 - (out["of_which_in_universe_today_ge_100m"]
                                        / out["cy2013_filers_revenue_ge_100m"])
    (C.RESULTS / "survivorship.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
