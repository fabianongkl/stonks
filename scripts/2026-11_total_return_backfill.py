"""One-time backfill, November 2026 review, proposals A and B (v0.3).

Until v0.3 every forward return and every paper book was PRICE-ONLY: a
dividend inside the window read as a loss (ARI's $4.10 special dividend
turned a ~+2% holding into a recorded -63%) and a split would have read as a
crash.  Going forward the scan downloads dividends and splits with its prices
and accounts for them.  This script brings the history onto the same basis:

  1. downloads dividend/split history for every symbol ever scored (plus SPY)
     and stores it in corporate_actions;
  2. measures each scan's TRUE price date (scans.price_asof) by matching its
     stored closes against the price history.  Cloud runs that started
     between ~00:00 and ~01:40 UTC received the previous day's bars for most
     stocks, so 20 scans hold prices one trading day older than their label
     (and a few are mixed).  A dividend window built on the label would
     credit an ex-date the recorded price never reached (CTVA: -12% recorded
     as +484%).  Labels are left as they are; price_asof is what every
     dividend/split window uses;
  3. keeps each recorded outcome's price-only return in outcomes.price_return
     and rewrites fwd_return as the total return over the same window (the
     recorded entry and exit prices are untouched; only dividends and splits
     with ex-date inside the window are added).  Where an event falls next to
     a window edge, the edge is resolved per stock by matching that stock's
     own recorded price, which also handles the mixed scans.  scan_eval and
     factor_ic are recomputed; price-only versions are archived in
     scan_eval_price_only / factor_ic_price_only;
  4. credits every paper book with the dividends (and splits) on positions it
     held over an ex-date, as DIV / SPLIT ledger rows dated on the ex-date,
     and restates each snapshot's cash by the dividends received up to the
     snapshot's price date (pf_snapshots.dividends_cum), so the equity curves
     carry no artificial jump.

Past scores (including the issuance metric) are NOT rewritten: they are the
record of what the model said at the time.  Split-adjusted issuance applies
from the next scan.

Idempotent: outcomes are recomputed from price_return, ledger events are
booked once, snapshots are restated by difference against dividends_cum.

    python scripts/2026-11_total_return_backfill.py [panel.parquet]

The optional parquet caches the downloaded price panel for re-runs.
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screener import db, learning, portfolio  # noqa: E402
from screener.data import prices  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill")
EXACT = 1e-3          # a recorded close "is" a day's close within 0.1%
NEAR_DAYS = 6         # calendar days around a window edge treated as ambiguous

conn = portfolio.connect()

# 1. Corporate actions ------------------------------------------------------
cache = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if cache and cache.exists():
    px = pd.read_parquet(cache)
else:
    syms = sorted({r[0] for r in conn.execute("SELECT DISTINCT symbol FROM scores")}
                  | {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM pf_txns")}
                  | {"SPY"})
    log.info("Downloading prices with dividends/splits for %d symbols", len(syms))
    px = prices._download(syms)
    if cache:
        px.to_parquet(cache, index=False)
ev = prices.corporate_actions(px)
db.save_corporate_actions(conn, ev)
log.info("corporate_actions: %d events (%d DIV, %d SPLIT) over %d symbols",
         len(ev), int((ev["kind"] == "DIV").sum()), int((ev["kind"] == "SPLIT").sum()),
         ev["symbol"].nunique())

# Raw close in the share terms of its own day: Yahoo's split-adjusted close
# times every split that happened after that day.
px = px.copy()
px["date"] = px["date"].astype(str)
px = px[px["date"] >= "2026-08-01"].sort_values(["symbol", "date"])
f = px["split"].where(px["split"] > 0, 1.0)
after = (f.iloc[::-1].groupby(px["symbol"].iloc[::-1]).cumprod().iloc[::-1]) / f
px["raw"] = px["close_split"] * after
raw = px.pivot_table(index="date", columns="symbol", values="raw")
dates = list(raw.index)


def candidates(label: str, back: int = 7) -> list[str]:
    lo = (date.fromisoformat(label) - timedelta(days=back)).isoformat()
    return [d for d in dates if lo <= d <= label]


def measure_asof(closes: pd.Series, label: str) -> tuple[str, float]:
    cols = [s for s in closes.index if s in raw.columns]
    best, share = label, 0.0
    for d in candidates(label):
        m = float(((raw.loc[d, cols] / closes[cols] - 1).abs() < 1e-4).mean())
        if m > share:
            best, share = d, m
    return (best, share) if share >= 0.5 else (label, share)


# 2. True price date of every scan (and archived run) -------------------------
asof = {}
for table, score_table in (("scans", "scores"), ("scans_superseded", "scores_superseded")):
    for sid, label in conn.execute(f"SELECT scan_id, scan_date FROM {table}").fetchall():
        closes = pd.read_sql_query(f"SELECT symbol, close FROM {score_table} WHERE scan_id=?",
                                   conn, params=(sid,)).set_index("symbol")["close"]
        d, share = measure_asof(closes, label)
        asof[sid] = d
        if table == "scans":
            conn.execute("UPDATE scans SET price_asof=? WHERE scan_id=?", (d, sid))
        note = "" if share >= 0.9 else ("  MIXED" if share >= 0.5 else
                                        "  UNMATCHED (intraday prices?), kept label")
        if d != label or note:
            log.info("%s %s: prices are from %s (%.1f%% exact)%s",
                     table, label, d, share * 100, note)
conn.commit()

# 3. Outcomes -> total return ------------------------------------------------
conn.execute("UPDATE outcomes SET price_return = fwd_return WHERE price_return IS NULL")
for t in ("scan_eval", "factor_ic"):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        (f"{t}_price_only",)).fetchone():
        conn.execute(f"CREATE TABLE {t}_price_only AS SELECT * FROM {t}")
conn.commit()

events = pd.read_sql_query(
    "SELECT symbol, ex_date, factor FROM corporate_actions WHERE factor IS NOT NULL", conn)
ev_by_sym = {s: g[["ex_date", "factor"]].to_numpy() for s, g in events.groupby("symbol")}


def resolve(sym: str, price: float, default: str, label: str) -> str:
    """The day this stock's recorded price is actually from, if an exact
    match exists near the window edge; else the scan-level price date."""
    if sym not in raw.columns or not (price and price > 0):
        return default
    hits = [d for d in candidates(label) if
            abs(raw.at[d, sym] / price - 1) < EXACT] if price == price else []
    if not hits or default in hits:
        return default
    return hits[-1]


def near(a: str, b: str) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= NEAR_DAYS


# Which run evaluated each scan?  elapsed_days was computed between the
# original (runner-date) labels; the earliest run at exactly that distance
# marked the exit, at ITS price date.
runs = pd.concat([
    pd.read_sql_query("SELECT scan_id, scan_date, run_label, created_at FROM scans", conn),
    pd.read_sql_query("SELECT scan_id, scan_date, run_label, created_at FROM scans_superseded", conn),
]).sort_values("created_at")
pairs = pd.read_sql_query(
    "SELECT DISTINCT o.scan_id, o.horizon_days, o.elapsed_days, s.scan_date, s.run_label "
    "FROM outcomes o JOIN scans s USING (scan_id)", conn)
edge_resolved = 0
for _, p in pairs.iterrows():
    sid, h = int(p["scan_id"]), int(p["horizon_days"])
    cnt = runs["run_label"].map(lambda L: int(np.busday_count(p["run_label"], L)))
    hit = runs[cnt == p["elapsed_days"]]
    if hit.empty:
        raise SystemExit(f"no evaluating run found for scan {sid}")
    xr = hit.iloc[0]
    entry_d, exit_d = asof[sid], asof[int(xr["scan_id"])]
    entry_lab, exit_lab = p["scan_date"], xr["scan_date"]
    o = pd.read_sql_query(
        "SELECT o.symbol, o.price_return, s.close FROM outcomes o JOIN scores s "
        "ON s.scan_id=o.scan_id AND s.symbol=o.symbol "
        "WHERE o.scan_id=? AND o.horizon_days=?", conn, params=(sid, h)).set_index("symbol")
    tr = 1.0 + o["price_return"]
    touched = 0
    for sym in o.index.intersection(list(ev_by_sym)):
        evs = ev_by_sym[sym]
        a, b = entry_d, exit_d
        if any(near(e, a) for e in evs[:, 0]):
            a2 = resolve(sym, o.at[sym, "close"], a, entry_lab)
            edge_resolved += a2 != a
            a = a2
        if any(near(e, b) for e in evs[:, 0]):
            b2 = resolve(sym, o.at[sym, "close"] * tr[sym], b, exit_lab)
            edge_resolved += b2 != b
            b = b2
        fac = float(np.prod([x for e, x in evs if a < e <= b]) or 1.0)
        if fac != 1.0:
            tr[sym] *= fac
            touched += 1
    tr = tr - 1.0
    conn.executemany(
        "UPDATE outcomes SET fwd_return=? WHERE scan_id=? AND horizon_days=? AND symbol=?",
        [(float(v), sid, h, s) for s, v in tr.items()])
    scores = db.get_scores(conn, sid).set_index("symbol")
    learning.record_eval(conn, sid, h, scores, tr)
    log.info("scan %s (prices %s -> %s, %dd): %d of %d outcomes include "
             "dividends/splits", sid, entry_d, exit_d, h, touched, len(o))
conn.commit()
log.info("%d window edges resolved per stock", edge_resolved)

# 4. Books: dividends and splits ------------------------------------------------
latest_id = portfolio.latest_scan(conn)[0]
through = asof[latest_id]
for b in portfolio.BOOKS:
    for a in portfolio.apply_corporate_actions(conn, b, through):
        log.info("[%s] %s", b, a)
if conn.execute("SELECT COUNT(*) FROM pf_txns WHERE side='SPLIT'").fetchone()[0]:
    raise SystemExit("a held position split: snapshot positions need restating "
                     "by hand, not by this script")

cols = {r[1] for r in conn.execute("PRAGMA table_info(pf_snapshots)")}
if "dividends_cum" not in cols:
    conn.execute("ALTER TABLE pf_snapshots ADD COLUMN dividends_cum REAL")
label_asof = {r[0]: r[1] for r in conn.execute("SELECT scan_date, price_asof FROM scans")}
divs = pd.read_sql_query(
    "SELECT book, date, shares*price AS amt FROM pf_txns WHERE side='DIV'", conn)
snaps = pd.read_sql_query("SELECT date, book, dividends_cum FROM pf_snapshots", conn)
for _, s in snaps.iterrows():
    d = label_asof.get(s["date"], s["date"])
    cum = round(float(divs[(divs["book"] == s["book"]) & (divs["date"] <= d)]["amt"].sum()), 2)
    prev = s["dividends_cum"] if s["dividends_cum"] == s["dividends_cum"] and s["dividends_cum"] is not None else 0.0
    delta = round(cum - prev, 2)
    if delta:
        conn.execute("UPDATE pf_snapshots SET cash = ROUND(cash + ?, 2), "
                     "total = ROUND(total + ?, 2) WHERE date=? AND book=?",
                     (delta, delta, s["date"], s["book"]))
    conn.execute("UPDATE pf_snapshots SET dividends_cum=? WHERE date=? AND book=?",
                 (cum, s["date"], s["book"]))
conn.commit()
log.info("snapshots restated with dividends received to each price date")
conn.close()
