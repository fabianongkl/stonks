"""Download point-in-time XBRL facts for the PGM screen.

    python -m backtest.pgm.fetch_facts

One SEC companyfacts request per company (<= 8/s, generic User-Agent from
screener.config, no personal details). Keeps EVERY filing of each fact with its
`filed` date; first-filed selection happens in signals.py. Checkpointed and
resumable. Also saves the universe snapshot used, so the run is reproducible.
"""

from __future__ import annotations

import logging

import pandas as pd

from screener import universe
from screener.data import fundamentals as fnd
from . import config as C

log = logging.getLogger("pgm.facts")

# field -> list of (taxonomy, tag, unit) in priority order
FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "revenue": [("us-gaap", t, "USD") for t in (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet", "SalesRevenueGoodsNet")],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "cogs": [("us-gaap", t, "USD") for t in (
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")],
    "op_income": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "cfo": [("us-gaap", t, "USD") for t in (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")],
    "assets": [("us-gaap", "Assets", "USD")],
    "shares_out": [("dei", "EntityCommonStockSharesOutstanding", "shares"),
                   ("us-gaap", "CommonStockSharesOutstanding", "shares")],
    "waso": [("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "shares")],
}


def extract(cik: int, j: dict) -> list[tuple]:
    rows = []
    facts = j.get("facts", {})
    for field, specs in FIELDS.items():
        for prio, (tax, tag, unit) in enumerate(specs):
            units = facts.get(tax, {}).get(tag, {}).get("units", {}).get(unit)
            if not units:
                continue
            for f in units:
                end, filed, val = f.get("end"), f.get("filed"), f.get("val")
                if not (end and filed) or val is None:
                    continue
                dur = fnd._dur_days(f)
                rows.append((cik, field, prio, end, -1 if dur is None else dur,
                             filed, f.get("form", ""), float(val)))
    return rows


COLS = ["cik", "field", "prio", "end", "dur", "filed", "form", "val"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    uni = universe.build_universe()
    uni.to_parquet(C.UNIVERSE, index=False)
    ciks = list(dict.fromkeys(int(c) for c in uni["cik"]))
    frames, done = [], set()
    if C.FACTS.exists():
        prev = pd.read_parquet(C.FACTS)
        done = set(prev["cik"].unique())
        frames.append(prev)
    todo = [c for c in ciks if c not in done]
    log.info("%d companies, %d cached, %d to fetch", len(ciks), len(done), len(todo))
    client = fnd._SecClient()
    buf: list[tuple] = []
    for i, cik in enumerate(todo, 1):
        j = client.get_companyfacts(cik)
        rows = extract(cik, j) if j else []
        buf.extend(rows or [(cik, "_none", 0, "1900-01-01", -1, "1900-01-01", "", 0.0)])
        if i % 250 == 0 or i == len(todo):
            frames.append(pd.DataFrame(buf, columns=COLS))
            pd.concat(frames, ignore_index=True).to_parquet(C.FACTS, index=False)
            buf = []
            log.info("progress %d/%d", i, len(todo))
    log.info("done")


if __name__ == "__main__":
    main()
