"""Download earnings-event dates from EDGAR submissions (PEAD test).

    python -m backtest.pgm.fetch_events

Rules: docs/PEAD_SCREEN.md section 2. Keeps 8-K (with items), 10-Q, 10-K
filings since 2012-06 with acceptance timestamps. Resumable.
"""

from __future__ import annotations

import logging

import pandas as pd

from screener.data import fundamentals as fnd
from . import config as C

log = logging.getLogger("pgm.events")
OUT = C.CACHE / "pead_events_raw.parquet"
SUB = "https://data.sec.gov/submissions/{name}"
FORMS = {"8-K", "10-Q", "10-K", "10-KT"}
SINCE = "2012-06-01"
COLS = ["cik", "form", "filingDate", "acceptanceDateTime", "items"]


def _rows(cik, block) -> list[tuple]:
    out = []
    for i, f in enumerate(block.get("form", [])):
        if f in FORMS and block["filingDate"][i] >= SINCE:
            out.append((cik, f, block["filingDate"][i],
                        block.get("acceptanceDateTime", [""] * (i + 1))[i],
                        block.get("items", [""] * (i + 1))[i] or ""))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ciks = list(dict.fromkeys(int(c) for c in pd.read_parquet(C.UNIVERSE)["cik"]))
    frames, done = [], set()
    if OUT.exists():
        prev = pd.read_parquet(OUT)
        done = set(prev["cik"].unique())
        frames.append(prev)
    todo = [c for c in ciks if c not in done]
    log.info("%d to fetch", len(todo))
    cl = fnd._SecClient()
    buf: list[tuple] = []
    for n, cik in enumerate(todo, 1):
        j = cl._get(SUB.format(name=f"CIK{cik:010d}.json"))
        rows = []
        if j:
            rows += _rows(cik, j["filings"]["recent"])
            for fl in j["filings"].get("files", []):
                if fl.get("filingTo", "") >= SINCE:
                    k = cl._get(SUB.format(name=fl["name"]))
                    if k:
                        rows += _rows(cik, k)
        buf += rows or [(cik, "_none", "1900-01-01", "", "")]
        if n % 250 == 0 or n == len(todo):
            frames.append(pd.DataFrame(buf, columns=COLS))
            pd.concat(frames, ignore_index=True).drop_duplicates().to_parquet(OUT, index=False)
            buf = []
            log.info("progress %d/%d", n, len(todo))
    log.info("done")


if __name__ == "__main__":
    main()
