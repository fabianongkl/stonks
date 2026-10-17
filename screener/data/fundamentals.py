"""Company fundamentals from SEC EDGAR — the primary source.

Two layers:

1. BULK LAYER — the XBRL "frames" API returns one accounting concept for
   EVERY filer in one request, so a full-market refresh costs ~110 small
   requests instead of 5,000+ per-company calls.

     https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2026Q2I.json

   Income-statement flows are built as TRAILING TWELVE MONTHS where possible:
   sum of the last four quarterly frames, else the latest annual spliced with
   post-year-end quarters (annual + new quarters − year-ago quarters), else
   the latest annual alone.  Cash-flow items are typically filed as
   year-to-date durations, which quarterly frames can't see — they fall back
   to the latest annual (documented limitation).

2. FALLBACK LAYER — companies still missing key data after the bulk layer
   (odd fiscal years, multi-class share structures like Alphabet that skip
   the dei shares tag) get a targeted per-company `companyfacts` fetch,
   cached for 30 days.  This is what restores the mega-caps the frames miss.

Shares outstanding coalesces FOUR tags (dei EntityCommonStockSharesOutstanding,
us-gaap CommonStockSharesOutstanding / CommonStockSharesIssued, and annual
WeightedAverageNumberOfSharesOutstandingBasic) — the dei tag alone misses
hundreds of filers including some of the largest companies on earth.

Prior-year shares and assets are also fetched, powering the net-issuance and
asset-growth metrics (see METHODOLOGY.md).
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

FRAMES = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

CF_FALLBACK_MAX = 1500          # per-run cap on per-company fallback fetches
CF_CACHE_DAYS = 30

# field -> ordered list of us-gaap tags to try (coalesced, first hit wins)
FLOW_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
# income-statement items are filed as discrete quarters -> TTM is possible;
# cash-flow items are filed year-to-date -> annual only (see module docstring)
TTM_FIELDS = ["revenue", "net_income", "op_income", "gross_profit"]

INSTANT_TAGS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}
SHARE_INSTANT_TAGS = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesIssued"),
]
SHARE_ANNUAL_TAG = "WeightedAverageNumberOfSharesOutstandingBasic"

# A fact older than this is refused outright.  Companies that switched from
# US GAAP to IFRS leave their old us-gaap tags behind forever: Agnico Eagle
# still serves Assets dated 2014, equity dated 2012 and revenue dated 2010.
# Without this guard the fallback extractor returns them as current, and the
# ratios built on them are nonsense.  Found 2026-10-17; see
# journal/review-2026-10.md.
MAX_FACT_AGE_DAYS = 550        # ~18 months

# Foreign private issuers (20-F / 40-F) report under IFRS, and the SEC files
# their XBRL under the `ifrs-full` taxonomy.  The bulk frames API does NOT
# serve ifrs-full (it 404s on every tag), so these companies are only
# reachable through the per-company companyfacts fallback below.
IFRS_FLOW_TAGS = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers",
                "RevenueFromSaleOfGoods", "RevenueFromRenderingOfServices"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "op_income": ["ProfitLossFromOperatingActivities"],
    "gross_profit": ["GrossProfit"],
    "cfo": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
}
IFRS_INSTANT_TAGS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["Equity", "EquityAttributableToOwnersOfParent"],
    "cash": ["CashAndCashEquivalents"],
}
IFRS_SHARE_TAGS = ["NumberOfSharesOutstanding", "IssuedCapitalNumberOfShares"]


class _SecClient:
    """Tiny rate-limited GET wrapper for data.sec.gov."""

    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers["User-Agent"] = config.SEC_USER_AGENT
        self._last = 0.0

    def _get(self, url: str) -> dict | None:
        wait = (1.0 / config.SEC_MAX_REQ_PER_SEC) - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            r = self.sess.get(url, timeout=120)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning("SEC fetch failed %s: %s", url.rsplit("/", 2)[-2:], e)
            return None

    def get_frame(self, taxonomy: str, tag: str, unit: str, period: str) -> dict | None:
        return self._get(FRAMES.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period))

    def get_companyfacts(self, cik: int) -> dict | None:
        return self._get(COMPANYFACTS.format(cik=cik))


def _frame_series(payload: dict | None) -> pd.Series:
    if not payload or "data" not in payload:
        return pd.Series(dtype=float)
    return pd.Series({int(r["cik"]): r["val"] for r in payload["data"]}, dtype=float)


# ---------------------------------------------------------------------------
# Period bookkeeping
# ---------------------------------------------------------------------------

def _annual_periods() -> tuple[str, str]:
    y = date.today().year
    if date.today().month >= 6:
        return f"CY{y-1}", f"CY{y-2}"
    return f"CY{y-2}", f"CY{y-3}"


def _recent_quarters(n: int = 6) -> list[str]:
    """Last n calendar quarters whose filings should exist, oldest first.

    10-Qs are due ~40-45 days after quarter end, so the newest usable quarter
    is the last one that ended at least ~2 months ago (month 9 -> Q2, not the
    still-running Q3).
    """
    y, m = date.today().year, date.today().month
    yy, qq = y, (m - 3) // 3
    if qq <= 0:
        yy, qq = y - 1, qq + 4
    out = []
    for _ in range(n):
        out.append((yy, qq))
        qq -= 1
        if qq == 0:
            yy, qq = yy - 1, 4
    return [f"CY{a}Q{b}" for a, b in reversed(out)]


def _instant_periods(n: int = 4, offset: int = 0) -> list[str]:
    """Recent instantaneous frame labels, newest first, optionally shifted
    back by `offset` quarters (offset=4 -> the year-ago window)."""
    qs = _recent_quarters(n + offset)
    sel = qs[: len(qs) - offset] if offset else qs
    return [f"{q}I" for q in reversed(sel[-n:])]


# ---------------------------------------------------------------------------
# Bulk layer
# ---------------------------------------------------------------------------

def _coalesce_periods(client: _SecClient, tags: list[tuple[str, str]], unit: str,
                      periods: list[str], stop_at: int | None = None) -> pd.Series:
    """Try periods newest-first across tags; first value per cik wins."""
    merged = pd.Series(dtype=float)
    for period in periods:
        for taxonomy, tag in tags:
            merged = merged.combine_first(
                _frame_series(client.get_frame(taxonomy, tag, unit, period)))
        if stop_at and len(merged) > stop_at:
            break
    return merged


def _flow_wide(client: _SecClient, tags: list[str], periods: list[str]) -> pd.DataFrame:
    """cik x period matrix for one flow field, coalescing across tags."""
    cols = {}
    for period in periods:
        s = pd.Series(dtype=float)
        for tag in tags:
            s = s.combine_first(_frame_series(
                client.get_frame("us-gaap", tag, "USD", period)))
        cols[period] = s
    return pd.DataFrame(cols)


def _ttm_from_wide(wide: pd.DataFrame, quarters: list[str],
                   annual_now: str, annual_prior: str) -> pd.Series:
    """TTM: sum of newest 4 quarters, else annual+splice, else annual."""
    q4 = quarters[-4:]
    have_all4 = wide[q4].notna().all(axis=1)
    ttm_q = wide[q4].sum(axis=1).where(have_all4)

    # splice: annual + quarters after the annual year − same quarters year before
    ann_year = int(annual_now[2:6])
    post = [q for q in quarters if int(q[2:6]) == ann_year + 1]
    splice = wide.get(annual_now, pd.Series(dtype=float)).copy()
    ok = splice.notna()
    for q in post:
        prior_q = f"CY{ann_year}{q[6:]}"
        if q in wide.columns and prior_q in wide.columns:
            pair_ok = wide[q].notna() & wide[prior_q].notna()
            splice = splice.where(~pair_ok, splice + wide[q] - wide[prior_q])
    splice = splice.where(ok)

    annual_fallback = wide.get(annual_now, pd.Series(dtype=float)).combine_first(
        wide.get(annual_prior, pd.Series(dtype=float)))
    return ttm_q.combine_first(splice).combine_first(annual_fallback)


def _fetch_bulk(client: _SecClient) -> pd.DataFrame:
    annual_now, annual_prior = _annual_periods()
    quarters = _recent_quarters(6)
    cols: dict[str, pd.Series] = {}

    log.info("EDGAR frames: flows (TTM where possible; %s..%s + %s/%s) ...",
             quarters[0], quarters[-1], annual_now, annual_prior)
    for field, tags in FLOW_TAGS.items():
        periods = [annual_now, annual_prior] + (quarters if field in TTM_FIELDS else [])
        wide = _flow_wide(client, tags, periods)
        if field in TTM_FIELDS:
            cols[field] = _ttm_from_wide(wide, quarters, annual_now, annual_prior)
        else:
            cols[field] = wide[annual_now].combine_first(wide[annual_prior])
        if field == "revenue":
            cols["revenue_prior"] = wide[annual_prior]

    log.info("EDGAR frames: balance-sheet instants ...")
    for field, tags in INSTANT_TAGS.items():
        cols[field] = _coalesce_periods(
            client, [("us-gaap", t) for t in tags], "USD",
            _instant_periods(4), stop_at=4000)
    cols["assets_prior"] = _coalesce_periods(
        client, [("us-gaap", "Assets")], "USD", _instant_periods(3, offset=4),
        stop_at=4000)

    log.info("EDGAR frames: shares outstanding (4-tag coalesce) ...")
    shares = _coalesce_periods(client, SHARE_INSTANT_TAGS, "shares",
                               _instant_periods(4))
    for p in (annual_now, annual_prior):
        shares = shares.combine_first(_frame_series(
            client.get_frame("us-gaap", SHARE_ANNUAL_TAG, "shares", p)))
    cols["shares_out"] = shares

    shares_prior = _coalesce_periods(client, SHARE_INSTANT_TAGS, "shares",
                                     _instant_periods(3, offset=4))
    shares_prior = shares_prior.combine_first(_frame_series(
        client.get_frame("us-gaap", SHARE_ANNUAL_TAG, "shares", annual_prior)))
    cols["shares_prior"] = shares_prior

    df = pd.DataFrame(cols)
    df.index.name = "cik"
    # Claim US-GAAP only where the frames API returned actual us-gaap
    # financial statements.  Shares outstanding comes from the dei
    # taxonomy, which every filer uses regardless of accounting standard,
    # so shares alone must not label a company as a US-GAAP reporter —
    # otherwise an IFRS filer would be mislabelled and its real standard
    # overridden downstream.
    has_gaap = df[["assets", "revenue", "net_income", "equity"]].notna().any(axis=1)
    df["reporting_standard"] = pd.Series(
        np.where(has_gaap, "US-GAAP", None), index=df.index, dtype=object)
    return df


# ---------------------------------------------------------------------------
# Fallback layer: per-company companyfacts
# ---------------------------------------------------------------------------

_CF_CACHE = config.CACHE_DIR / "companyfacts_fill.parquet"
_CF_FIELDS = ["revenue", "net_income", "op_income", "gross_profit", "cfo",
              "capex", "assets", "liabilities", "equity", "cash",
              "assets_prior", "shares_out", "shares_prior", "revenue_prior"]


def _dur_days(f: dict) -> int | None:
    try:
        d0 = datetime.fromisoformat(f["start"])
        d1 = datetime.fromisoformat(f["end"])
        return (d1 - d0).days
    except Exception:
        return None


def _fresh(units: list[dict]) -> list[dict]:
    """Drop facts whose period END is older than MAX_FACT_AGE_DAYS."""
    cutoff = datetime.now() - timedelta(days=MAX_FACT_AGE_DAYS)
    out = []
    for f in units:
        try:
            if datetime.fromisoformat(f["end"]) >= cutoff:
                out.append(f)
        except Exception:
            continue
    return out


def _cf_flow(units: list[dict]) -> tuple[float | None, float | None]:
    """(TTM-ish, prior-annual) from a companyfacts USD fact list.

    `units` must already be freshness-filtered; a prior-year comparison is
    allowed to reach a little further back than the headline figure.
    """
    qs = sorted((f for f in units if (_dur_days(f) or 0) in range(80, 101)),
                key=lambda f: f["end"])
    dedup = {f["end"]: f["val"] for f in qs}
    ends = sorted(dedup)
    annuals = sorted((f for f in units if 330 <= (_dur_days(f) or 0) <= 400),
                     key=lambda f: f["end"])
    ann = annuals[-1]["val"] if annuals else None
    ann_prior = annuals[-2]["val"] if len(annuals) >= 2 else None
    if len(ends) >= 4:
        last4 = ends[-4:]
        newest = datetime.fromisoformat(last4[-1])
        oldest = datetime.fromisoformat(last4[0])
        if (newest - oldest).days < 320 and (datetime.now() - newest).days < 200:
            return float(sum(dedup[e] for e in last4)), ann_prior
    return (float(ann) if ann is not None else None,
            float(ann_prior) if ann_prior is not None else None)


def _cf_instant(units: list[dict]) -> tuple[float | None, float | None]:
    """(latest, ~year-ago) instantaneous values."""
    pts = sorted({f["end"]: f["val"] for f in units if f.get("end")}.items())
    if not pts:
        return None, None
    latest_end, latest_val = pts[-1]
    d1 = datetime.fromisoformat(latest_end)
    prior = None
    for end, val in reversed(pts):
        dd = (d1 - datetime.fromisoformat(end)).days
        if 300 <= dd <= 470:
            prior = val
            break
    return float(latest_val), (float(prior) if prior is not None else None)


def _extract_one(facts: dict, taxonomy: str, flow_tags: dict,
                 instant_tags: dict, share_tags: list, dei: dict) -> dict:
    """Pull our fields out of ONE taxonomy's facts (us-gaap or ifrs-full).

    USD facts ONLY, deliberately.  Foreign issuers often report in their
    home currency (of 231 gate-excluded stocks: 141 file Assets in USD, 42
    in CAD, 21 in EUR, the rest scattered).  Market cap is always USD, so
    pairing a CAD balance sheet with a USD market cap would silently inflate
    every value ratio by the exchange rate.  Until the project has an FX
    layer, non-USD reporters are left without fundamentals and the
    completeness gate correctly declines to score them.  A company that
    files in both takes the USD series.
    """
    src = facts.get(taxonomy, {})
    out: dict[str, float] = {}

    for field, tags in flow_tags.items():
        for tag in tags:
            units = _fresh(src.get(tag, {}).get("units", {}).get("USD") or [])
            if not units:
                continue
            ttm, prior = _cf_flow(units)
            if ttm is not None:
                out[field] = ttm
                if field == "revenue" and prior is not None:
                    out["revenue_prior"] = prior
                break

    for field, tags in instant_tags.items():
        for tag in tags:
            units = _fresh(src.get(tag, {}).get("units", {}).get("USD") or [])
            if not units:
                continue
            latest, prior = _cf_instant(units)
            if latest is not None:
                out[field] = latest
                if field == "assets" and prior is not None:
                    out["assets_prior"] = prior
                break

    # Shares: the dei tag is taxonomy-independent and is tried first for both.
    for tax, tag in ([("dei", "EntityCommonStockSharesOutstanding")]
                     + [(taxonomy, t) for t in share_tags]):
        store = dei if tax == "dei" else src
        units = _fresh(store.get(tag, {}).get("units", {}).get("shares") or [])
        if not units:
            continue
        latest, prior = _cf_instant(units)
        if latest:
            out["shares_out"] = latest
            if prior:
                out["shares_prior"] = prior
            break
    return out


def _extract_companyfacts(j: dict) -> dict:
    """Fundamentals for one company, from whichever accounting standard it
    actually reports under.

    US GAAP is tried first; if it yields less than IFRS does, the IFRS
    reading wins.  This is what makes foreign private issuers (20-F / 40-F
    filers) visible at all — the bulk frames API serves only us-gaap, so
    without this they arrive with no fundamentals and are correctly refused
    by the completeness gate.  Comparing field counts rather than simply
    preferring us-gaap matters because companies that MIGRATED to IFRS keep
    their abandoned us-gaap tags on file (see MAX_FACT_AGE_DAYS).
    """
    facts = j.get("facts", {})
    dei = facts.get("dei", {})

    gaap = _extract_one(facts, "us-gaap", FLOW_TAGS, INSTANT_TAGS,
                        [t for _, t in SHARE_INSTANT_TAGS
                         if _ != "dei"] + [SHARE_ANNUAL_TAG], dei)
    ifrs = _extract_one(facts, "ifrs-full", IFRS_FLOW_TAGS, IFRS_INSTANT_TAGS,
                        IFRS_SHARE_TAGS, dei)

    core = ("assets", "equity", "revenue", "net_income")
    g_core = sum(1 for k in core if k in gaap)
    i_core = sum(1 for k in core if k in ifrs)
    if i_core > g_core or (i_core == g_core and len(ifrs) > len(gaap)):
        out, standard = dict(ifrs), "IFRS"
    else:
        out, standard = dict(gaap), "US-GAAP"
    if out:
        out["reporting_standard"] = standard
    return out


def companyfacts_fill(df: pd.DataFrame, ciks_needed: list[int]) -> pd.DataFrame:
    """Fill missing fundamental fields for specific CIKs via companyfacts.

    Returns df with gaps filled.  Results are cached for CF_CACHE_DAYS days.
    """
    if not ciks_needed:
        return df
    cache = (pd.read_parquet(_CF_CACHE) if _CF_CACHE.exists()
             else pd.DataFrame(columns=["cik", "fetched", "reporting_standard"]
                               + _CF_FIELDS))
    cutoff = (date.today() - timedelta(days=CF_CACHE_DAYS)).isoformat()
    fresh = cache[cache["fetched"] >= cutoff]
    have = set(fresh["cik"])
    to_fetch = [c for c in dict.fromkeys(ciks_needed) if c not in have][:CF_FALLBACK_MAX]

    if to_fetch:
        log.info("companyfacts fallback: fetching %d companies (~%.0f min) ...",
                 len(to_fetch), len(to_fetch) / config.SEC_MAX_REQ_PER_SEC / 60)
        client = _SecClient()
        rows = []
        for i, cik in enumerate(to_fetch, 1):
            j = client.get_companyfacts(cik)
            row = {"cik": cik, "fetched": date.today().isoformat()}
            if j:
                row.update(_extract_companyfacts(j))
            rows.append(row)
            if i % 200 == 0:
                log.info("  companyfacts progress: %d/%d", i, len(to_fetch))
        newly = pd.DataFrame(rows)
        cache = pd.concat([cache[~cache["cik"].isin(set(newly["cik"]))], newly],
                          ignore_index=True)
        for f in _CF_FIELDS + ["reporting_standard"]:
            if f not in cache.columns:
                cache[f] = pd.NA
        cache.to_parquet(_CF_CACHE, index=False)
        fresh = cache[cache["fetched"] >= cutoff]

    sel = fresh[fresh["cik"].isin(set(ciks_needed))].set_index("cik")
    fill = sel[_CF_FIELDS].astype(float)
    filled = df.combine_first(fill.reindex(df.index.union(fill.index)))

    # Reporting standard is a label, not a number — carried separately so it
    # survives the float coercion above.
    if "reporting_standard" in sel.columns:
        std = sel["reporting_standard"].reindex(filled.index)
        if "reporting_standard" in filled.columns:
            # the fallback compared BOTH taxonomies for these companies, so
            # its verdict wins over the bulk layer's assumption
            filled["reporting_standard"] = std.combine_first(
                filled["reporting_standard"])
        else:
            filled["reporting_standard"] = std
        ifrs_n = int((std == "IFRS").sum())
        log.info("companyfacts fallback: %d companies read under IFRS", ifrs_n)

    n = (df.reindex(filled.index)["shares_out"].isna()
         & filled["shares_out"].notna()).sum()
    log.info("companyfacts fallback: recovered shares data for %d companies", int(n))
    return filled


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch_fundamentals(use_cache: bool = True) -> pd.DataFrame:
    """DataFrame indexed by CIK: revenue, revenue_prior, net_income, op_income,
    gross_profit, cfo, capex, assets, assets_prior, liabilities, equity, cash,
    shares_out, shares_prior.  TTM for income-statement flows where possible.
    """
    week = date.today().isocalendar()
    cache = config.CACHE_DIR / f"fundamentals_v2_{week.year}w{week.week:02d}.parquet"
    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    client = _SecClient()
    df = _fetch_bulk(client)
    log.info("Fundamentals (bulk): %d filers with at least one field", len(df))
    df.to_parquet(cache)
    return df
