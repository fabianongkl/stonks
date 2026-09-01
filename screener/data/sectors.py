"""Sector classification from SEC SIC codes.

Every SEC filer has an assigned Standard Industrial Classification (SIC) code,
retrievable from EDGAR's company browse endpoint (~17KB of Atom XML per
company). We fetch each company's SIC once and cache it permanently — SIC
codes essentially never change — then map SIC ranges onto ~12 broad sectors.

Why sectors matter: value and low-volatility metrics differ structurally by
industry (banks always look "cheap" on book value; utilities are always
"low vol"). Ranking within sectors compares like with like, so the composite
stops being a disguised sector bet.  The buckets are deliberately coarse —
they only need to be big enough for cross-sectional ranking, not a perfect
taxonomy.
"""

from __future__ import annotations

import logging
import re
import time

import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

# Primary: the submissions API on data.sec.gov (built for automation; the
# "sic" field sits in the first few KB, so we stream-read one chunk and close
# instead of downloading the whole multi-MB filing history).
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_SIC_JSON_RE = re.compile(r'"sic"\s*:\s*"?(\d+)')
# Fallback: the browse-edgar Atom endpoint on www.sec.gov (throttled hard
# under sustained parallel load — usable for stragglers only).
ATOM_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            "&CIK={cik:010d}&type=10-K&count=1&output=atom")
_SIC_RE = re.compile(r"<assigned-sic>(\d+)</assigned-sic>")

CACHE = config.CACHE_DIR / "sic_map.parquet"   # permanent, appended to


def sic_to_sector(sic: int) -> str:
    """Map a SIC code to a broad sector bucket."""
    s = int(sic)
    if 100 <= s <= 999:
        return "Materials"
    if 1000 <= s <= 1299 or 1400 <= s <= 1499:
        return "Materials"
    if 1300 <= s <= 1399 or 2900 <= s <= 2999:
        return "Energy"
    if 1500 <= s <= 1799:
        return "Industrials"
    if 2000 <= s <= 2111 or s in (2840, 2841, 2842, 2843, 2844):
        return "Consumer Staples"
    if 2200 <= s <= 2399 or 3900 <= s <= 3999:
        return "Consumer Discretionary"
    if 2833 <= s <= 2836 or 3841 <= s <= 3851 or 8000 <= s <= 8099 or s == 8731:
        return "Healthcare"
    if 2400 <= s <= 2899:
        return "Materials"
    if 3570 <= s <= 3579 or 3670 <= s <= 3699 or 3800 <= s <= 3829 or 7370 <= s <= 7379:
        return "Technology"
    if 3000 <= s <= 3799:
        return "Industrials"
    if 4000 <= s <= 4799:
        return "Industrials"
    if 4800 <= s <= 4899 or 2700 <= s <= 2799 or 7800 <= s <= 7899:
        return "Communication"
    if 4900 <= s <= 4999:
        return "Utilities"
    if 5000 <= s <= 5999:
        return "Consumer Discretionary"
    if s == 6798 or 6500 <= s <= 6599:
        return "Real Estate"
    if 6000 <= s <= 6999:
        return "Financials"
    if 7000 <= s <= 7299 or 7900 <= s <= 7999:
        return "Consumer Discretionary"
    if 7300 <= s <= 7369 or 7380 <= s <= 7699 or 8100 <= s <= 8999:
        return "Industrials"
    return "Other"


def _load_cache() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    return pd.DataFrame(columns=["cik", "sic"]).astype({"cik": "int64", "sic": "int64"})


class _RateGate:
    """Cross-thread rate limiter keeping total requests under the SEC cap."""

    def __init__(self, per_sec: float):
        import threading
        self.lock = threading.Lock()
        self.interval = 1.0 / per_sec
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            slot = max(now, self.next_at)
            self.next_at = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


def _fetch_one(sess: requests.Session, gate: "_RateGate", cik: int) -> dict:
    gate.wait()
    sic = 0
    try:
        r = sess.get(SUBMISSIONS_URL.format(cik=cik), timeout=30, stream=True)
        if r.ok:
            head = next(r.iter_content(8192), b"").decode("utf-8", errors="ignore")
            m = _SIC_JSON_RE.search(head)
            if m:
                sic = int(m.group(1))
        r.close()
    except Exception as e:
        log.debug("SIC fetch failed for CIK %d: %s", cik, e)
    if not sic:                     # straggler: try the Atom endpoint once
        try:
            r = sess.get(ATOM_URL.format(cik=cik), timeout=30)
            if r.ok:
                m = _SIC_RE.search(r.text)
                if m:
                    sic = int(m.group(1))
        except Exception:
            pass
    return {"cik": cik, "sic": sic}


def _fetch_parallel(cache: pd.DataFrame, missing: list[int]) -> pd.DataFrame:
    """Threaded fetch (latency-bound work) capped at the SEC rate limit,
    checkpointing the cache every ~500 results so progress survives an abort."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    gate = _RateGate(config.SEC_MAX_REQ_PER_SEC)
    sess = requests.Session()
    sess.headers["User-Agent"] = config.SEC_USER_AGENT
    rows: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_one, sess, gate, cik) for cik in missing]
        for fut in as_completed(futures):
            rows.append(fut.result())
            done += 1
            if done % 500 == 0 or done == len(missing):
                cache = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True)
                cache = cache.drop_duplicates("cik")
                cache.to_parquet(CACHE, index=False)
                rows = []
                log.info("  SIC progress: %d/%d (checkpointed)", done, len(missing))
    return cache


def get_sectors(ciks: list[int]) -> pd.DataFrame:
    """Return DataFrame [cik, sic, sector] for the requested CIKs.

    Cached CIKs are free; new ones are fetched (~8/s, one-time cost per
    company — the first full-universe run takes ~10 minutes, then it's
    incremental forever).
    """
    cache = _load_cache()
    have = set(cache["cik"])
    missing = [c for c in dict.fromkeys(ciks) if c not in have]

    if missing:
        log.info("Fetching SIC codes for %d new companies (one-time cost) ...",
                 len(missing))
        cache = _fetch_parallel(cache, missing)

    out = cache[cache["cik"].isin(set(ciks))].copy()
    out["sector"] = out["sic"].map(lambda s: sic_to_sector(s) if s else "Other")
    return out[["cik", "sic", "sector"]]
