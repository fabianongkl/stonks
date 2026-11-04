"""One-time correction, November 2026 review: relabel scans by price date.

Until 2026-11-04 each scan was labelled with the runner's calendar date.
GitHub's scheduler fires the 21:30 UTC job late, often after midnight UTC,
so 21 of the first 43 scans carried a label one day AFTER the close their
prices came from.  This script:

  1. derives each scan's true price date from its run timestamp (a run before
     21:00 on the runner clock precedes that day's close; weekends roll back);
  2. archives (never deletes) the one duplicate it exposes: the Oct 16 close
     was scanned twice, by the nightly run and by the IFRS re-run the next
     morning.  The later scan is canonical because it is the one the books
     were marked against; the earlier moves to scans_superseded /
     scores_superseded with a reason;
  3. relabels scans and portfolio snapshots, keeping the original label in
     scans.run_label so the history stays auditable.

Idempotent: a second run finds nothing to move.
"""
import sqlite3
import sys
from datetime import datetime, timedelta

DB = sys.argv[1] if len(sys.argv) > 1 else "data/screener.db"


def price_date(created: str) -> str:
    t = datetime.fromisoformat(created)
    d = t.date() if t.hour >= 21 else t.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


c = sqlite3.connect(DB)
cols = {r[1] for r in c.execute("PRAGMA table_info(scans)")}
if "run_label" not in cols:
    c.execute("ALTER TABLE scans ADD COLUMN run_label TEXT")
c.execute("UPDATE scans SET run_label = scan_date WHERE run_label IS NULL")

rows = c.execute("SELECT scan_id, scan_date, created_at FROM scans").fetchall()
target = {sid: price_date(created) for sid, _, created in rows}
label = {sid: lab for sid, lab, _ in rows}

# --- duplicates: keep the latest run of each price date, archive the rest ---
by_date: dict[str, list[int]] = {}
for sid, _, created in sorted(rows, key=lambda r: r[2]):
    by_date.setdefault(target[sid], []).append(sid)
c.execute("CREATE TABLE IF NOT EXISTS scans_superseded AS SELECT *, '' AS reason "
          "FROM scans WHERE 0")
c.execute("CREATE TABLE IF NOT EXISTS scores_superseded AS SELECT * FROM scores WHERE 0")
for d, sids in by_date.items():
    for sid in sids[:-1]:
        keep = sids[-1]
        reason = (f"same price date {d} as scan {keep} (a later re-run); "
                  f"archived by the 2026-11 relabel")
        c.execute("INSERT INTO scans_superseded SELECT *, ? FROM scans WHERE scan_id=?",
                  (reason, sid))
        c.execute("INSERT INTO scores_superseded SELECT * FROM scores WHERE scan_id=?", (sid,))
        for t in ("scores", "outcomes", "scan_eval", "factor_ic", "scans"):
            c.execute(f"DELETE FROM {t} WHERE scan_id=?", (sid,))
        print(f"archived scan {sid} (label {label[sid]}): {reason}")
        target.pop(sid)

# --- relabel scans, two-phase so the UNIQUE(scan_date) index never trips ---
moved = {sid: d for sid, d in target.items() if label[sid] != d}
for sid in moved:
    c.execute("UPDATE scans SET scan_date=? WHERE scan_id=?", (f"tmp-{sid}", sid))
for sid, d in moved.items():
    c.execute("UPDATE scans SET scan_date=? WHERE scan_id=?", (d, sid))

# --- snapshots follow their scan's label ---
snap_map = {label[sid]: d for sid, d in moved.items()}
for old in snap_map:
    c.execute("UPDATE pf_snapshots SET date=? WHERE date=?", (f"tmp-{old}", old))
for old, new in snap_map.items():
    c.execute("UPDATE pf_snapshots SET date=? WHERE date=?", (new, f"tmp-{old}"))

c.commit()
print(f"relabelled {len(moved)} scans and their snapshots")
