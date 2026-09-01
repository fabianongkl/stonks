"""Fetch point-in-time fundamental facts for the whole universe.

    python -m backtest.fetch_pit

For every company, downloads EDGAR `companyfacts` and stores EVERY historical
fact for the fields the screener uses — including each fact's `filed` date.
That filing date is what makes the backtest honest: a simulated date in 2022
may only see facts that had actually been filed by then.

Output: backtest/cache/pit_facts.parquet with rows
    (cik, field, end, filed, dur_days, val)
dur_days = -1 marks instantaneous (balance-sheet) facts.

~5,200 downloads at the SEC rate limit ≈ 15-25 minutes; checkpointed every
250 companies, safe to re-run (resumes).
"""

from __future__ import annotations

import logging

import pandas as pd

from screener import universe
from screener.data import fundamentals as fnd
from . import config_bt

log = logging.getLogger("bt.pit")

OUT = config_bt.CACHE / "pit_facts.parquet"

# field -> (taxonomy, [tags], unit)
FIELD_TAGS: dict[str, tuple[str, list[str], str]] = {}
for f, tags in fnd.FLOW_TAGS.items():
    FIELD_TAGS[f] = ("us-gaap", tags, "USD")
for f, tags in fnd.INSTANT_TAGS.items():
    FIELD_TAGS[f] = ("us-gaap", tags, "USD")
FIELD_TAGS["shares"] = ("*", [t for _, t in fnd.SHARE_INSTANT_TAGS]
                        + [fnd.SHARE_ANNUAL_TAG], "shares")


def _extract(cik: int, j: dict) -> list[dict]:
    rows = []
    facts = j.get("facts", {})
    for field, (_tax, tags, unit) in FIELD_TAGS.items():
        for tag in tags:
            for tax in ("us-gaap", "dei"):
                units = facts.get(tax, {}).get(tag, {}).get("units", {}).get(unit)
                if not units:
                    continue
                for fct in units:
                    end, filed, val = fct.get("end"), fct.get("filed"), fct.get("val")
                    if not (end and filed) or val is None:
                        continue
                    dur = fnd._dur_days(fct)
                    rows.append({"cik": cik, "field": field, "end": end,
                                 "filed": filed,
                                 "dur_days": -1 if dur is None else dur,
                                 "val": float(val)})
    # Companies switch tags over the years, so we keep every tag's facts;
    # duplicates for the same (end, period-length) are dropped downstream.
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    uni = universe.build_universe()
    ciks = list(dict.fromkeys(uni["cik"].tolist()))

    done_ciks: set[int] = set()
    frames = []
    if OUT.exists():
        prev = pd.read_parquet(OUT)
        done_ciks = set(prev["cik"].unique())
        frames.append(prev)
        ciks = [c for c in ciks if c not in done_ciks]
        log.info("Resuming: %d companies cached, %d to fetch", len(done_ciks), len(ciks))

    client = fnd._SecClient()
    buf: list[dict] = []
    for i, cik in enumerate(ciks, 1):
        j = client.get_companyfacts(cik)
        if j:
            buf.extend(_extract(cik, j))
        else:
            buf.append({"cik": cik, "field": "_none", "end": "1900-01-01",
                        "filed": "1900-01-01", "dur_days": -1, "val": 0.0})
        if i % 250 == 0 or i == len(ciks):
            frames.append(pd.DataFrame(buf))
            pd.concat(frames, ignore_index=True).to_parquet(OUT, index=False)
            buf = []
            log.info("  PIT progress: %d/%d (checkpointed)", i, len(ciks))

    total = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log.info("DONE: %d companies, %d facts", total["cik"].nunique(), len(total))


if __name__ == "__main__":
    main()
