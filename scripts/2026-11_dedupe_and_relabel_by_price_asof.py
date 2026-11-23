"""One-time correction, decided 2026-11-24: finish the November audit.

The 2026-11 review (journal/review-2026-11.md, "Audit") found that cloud runs
starting ~00:00-01:40 UTC got the previous trading day's bars for most stocks,
so the first relabel (scripts/2026-11_relabel_scans_by_price_date.py) left
scans labelled one day after their prices.  scans.price_asof already drives
all the arithmetic; this script makes the labels and the record match it:

  1. archives the four duplicate price days, keeping the cleaner scan of each.
     Cleaner = more stocks priced on the day, checked 2026-11-24 against
     Yahoo daily bars for every symbol whose close differs between the pair:
        Sep 24  keep 28 (90% on the day) archive 27 (17%; mostly Sep 23 bars)
        Oct  6  keep 36 (94%)            archive 35 (9%;  mostly Oct 5 bars)
        Oct 23  keep 54 (87%)            archive 53 (19%; mostly Oct 22 bars)
        Nov  2  keep 59 (98%)            archive 60 (7%;  mostly Nov 3 bars)
  2. makes scan 48 (the Oct 16 nightly, true closes) canonical in place of
     scan 47 (the Oct 17 IFRS re-run, which reused an intraday price cache).
     Caveat recorded in the journal: 48 was scored before IFRS support
     shipped, so its ranks are the v0.2.1 model's.  Its 21-day outcome is
     rebuilt from the exit prices recorded for 47 (same exit run, Nov 16).
  3. relabels every scan to its price_asof (two-phase, UNIQUE-safe) and moves
     portfolio snapshots with their scan; snapshots of archived runs go.
     The original label stays in scans.run_label.

Ledger rows (pf_txns) are untouched: review trades stay at the prices and
dates they were executed at.  Archived scans keep their scores in
scores_superseded; their outcomes/eval rows are dropped (rebuildable).

Idempotent: a second run finds nothing to do.

    python scripts/2026-11_dedupe_and_relabel_by_price_asof.py [db]
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screener import db, learning  # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 else "data/screener.db"
ARCHIVE = {27: 28, 35: 36, 53: 54, 60: 59}       # archived scan -> kept scan
OLD_CANON, NEW_CANON, OCT16 = 47, 48, "2026-10-16"
EVAL_TABLES = ("outcomes", "scan_eval", "factor_ic")

c = sqlite3.connect(DB)
scan_cols = [r[1] for r in c.execute("PRAGMA table_info(scans)")]
sup_cols = {r[1] for r in c.execute("PRAGMA table_info(scans_superseded)")}
if "price_asof" not in sup_cols:
    c.execute("ALTER TABLE scans_superseded ADD COLUMN price_asof TEXT")
cols = ", ".join(scan_cols)


def label_of(sid: int) -> str | None:
    r = c.execute("SELECT scan_date FROM scans WHERE scan_id=?", (sid,)).fetchone()
    return r[0] if r else None


def archive(sid: int, reason: str) -> None:
    lab = label_of(sid)
    c.execute(f"INSERT INTO scans_superseded ({cols}, reason) "
              f"SELECT {cols}, ? FROM scans WHERE scan_id=?", (reason, sid))
    c.execute("INSERT INTO scores_superseded SELECT * FROM scores WHERE scan_id=?", (sid,))
    for t in ("scores", *EVAL_TABLES, "scans"):
        c.execute(f"DELETE FROM {t} WHERE scan_id=?", (sid,))
    print(f"archived scan {sid} (label {lab}): {reason}")


# --- 1. duplicate price days ----------------------------------------------
for sid, keep in ARCHIVE.items():
    lab = label_of(sid)
    if lab is None:
        continue
    d = c.execute("SELECT price_asof FROM scans WHERE scan_id=?", (sid,)).fetchone()[0]
    kd = c.execute("SELECT price_asof FROM scans WHERE scan_id=?", (keep,)).fetchone()[0]
    assert d == kd, f"scan {sid} ({d}) and {keep} ({kd}) are not the same price day"
    archive(sid, f"same price date {d} as scan {keep}, which has more stocks priced "
                 f"on that day; archived 2026-11-24 (price_asof dedupe)")
    # its snapshots duplicate the kept run's day; the kept run's take the date
    n = c.execute("DELETE FROM pf_snapshots WHERE date=?", (lab,)).rowcount
    print(f"  dropped {n} snapshots dated {lab}")

# --- 2. scan 48 canonical for Oct 16 --------------------------------------
if label_of(OLD_CANON) is not None:
    # exit prices 47's 21d evaluation used, recovered from its own record
    o47 = pd.read_sql_query(
        "SELECT o.symbol, o.elapsed_days, s.close*(1+o.price_return) AS exit_px "
        "FROM outcomes o JOIN scores s USING (scan_id, symbol) "
        "WHERE o.scan_id=? AND o.horizon_days=21", c, params=(OLD_CANON,))
    assert o47["elapsed_days"].nunique() == 1
    elapsed = int(o47["elapsed_days"].iloc[0])
    exit_px = o47.set_index("symbol")["exit_px"]

    archive(OLD_CANON, "Oct 16 IFRS re-run (2026-10-17 08:17 UTC) reused a price "
                       "cache built during the US session: intraday prices. Scan 48 "
                       "(true close) made canonical 2026-11-24")
    c.execute(f"INSERT INTO scans ({cols}) SELECT {cols} FROM scans_superseded "
              f"WHERE scan_id=?", (NEW_CANON,))
    c.execute("UPDATE scans SET price_asof=?, run_label=COALESCE(run_label, scan_date) "
              "WHERE scan_id=?", (OCT16, NEW_CANON))
    c.execute("INSERT INTO scores SELECT * FROM scores_superseded WHERE scan_id=?", (NEW_CANON,))
    c.execute("DELETE FROM scores_superseded WHERE scan_id=?", (NEW_CANON,))
    c.execute("DELETE FROM scans_superseded WHERE scan_id=?", (NEW_CANON,))
    print(f"restored scan {NEW_CANON} as canonical {OCT16}")

    # 21d outcome for 48, from the same exit prices (Nov 16 run)
    exit_day = None
    for d in pd.bdate_range(OCT16, periods=elapsed + 5).strftime("%Y-%m-%d"):
        if learning._trading_days_between(OCT16, d) == elapsed:
            exit_day = d
            break
    s48 = db.get_scores(c, NEW_CANON).set_index("symbol")
    now = exit_px.reindex(s48.index)
    missing = now[now.isna()].index.tolist()
    if missing:   # same survivorship guard as learning._evaluate
        ph = ",".join("?" * len(missing))
        lk = pd.read_sql_query(
            f"SELECT symbol, close FROM scores s JOIN scans c USING (scan_id) "
            f"WHERE symbol IN ({ph}) AND c.scan_date<=? ORDER BY c.scan_date",
            c, params=(*missing, exit_day))
        now = now.combine_first(lk.drop_duplicates("symbol", keep="last")
                                  .set_index("symbol")["close"])
    pr = (now / s48["close"] - 1).replace([float("inf"), float("-inf")], float("nan")).dropna()
    f = db.action_factors(c, OCT16, exit_day, pr.index)
    fwd = (1 + pr) * f.reindex(pr.index).fillna(1.0) - 1
    c.executemany(
        "INSERT OR REPLACE INTO outcomes (scan_id, symbol, horizon_days, elapsed_days,"
        " fwd_return, price_return) VALUES (?,?,?,?,?,?)",
        [(NEW_CANON, s, 21, elapsed, float(fwd[s]), float(pr[s])) for s in pr.index])
    learning.record_eval(c, NEW_CANON, 21, s48, fwd)
    print(f"scan {NEW_CANON}: 21d outcome rebuilt, {len(fwd)} stocks "
          f"(exit {exit_day}, {len(missing)} via survivorship guard)")

# --- 3. labels follow price_asof ------------------------------------------
rows = c.execute("SELECT scan_id, scan_date, price_asof FROM scans").fetchall()
moved = {sid: (lab, asof) for sid, lab, asof in rows if asof and lab != asof}
targets = [a for _, a in moved.values()]
assert len(set(targets)) == len(targets), "two scans would share a label"
stay = {lab for sid, lab, _ in rows if sid not in moved}
assert not (set(targets) & stay), "relabel would collide with an unmoved scan"
c.execute("UPDATE scans SET run_label = scan_date WHERE run_label IS NULL")
for sid in moved:
    c.execute("UPDATE scans SET scan_date=? WHERE scan_id=?", (f"tmp-{sid}", sid))
for sid, (_, asof) in moved.items():
    c.execute("UPDATE scans SET scan_date=? WHERE scan_id=?", (asof, sid))
for lab, _ in moved.values():
    c.execute("UPDATE pf_snapshots SET date=? WHERE date=?", (f"tmp-{lab}", lab))
for lab, asof in moved.values():
    c.execute("UPDATE pf_snapshots SET date=? WHERE date=?", (asof, f"tmp-{lab}"))
for sid, (lab, asof) in sorted(moved.items()):
    print(f"relabelled scan {sid}: {lab} -> {asof}")

c.commit()
print(f"done: {len(moved)} scans relabelled")
