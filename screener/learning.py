"""The self-improvement loop.

Two mechanisms, run automatically at the end of every scan:

1. OUTCOME TRACKING — for every past scan that has "matured" past a horizon
   (21 / 63 / 126 trading days), record how each scored stock actually
   performed, how the top decile did versus the universe median, and each
   factor's information coefficient (IC — the rank correlation between the
   factor's scores on scan day and subsequent returns).  An IC persistently
   above zero means the factor genuinely predicted returns.

2. WEIGHT ADAPTATION — once at least MIN_SCANS_BEFORE_LEARNING scans have
   matured at the primary horizon, factor weights drift toward the factors
   that have demonstrated predictive power (mean IC), at a deliberately slow
   learning rate and with a floor so no factor is ever fully abandoned.
   Every change is written to weights_history with its reasoning — the
   system's decisions are auditable forever.

Why ICs and not something fancier?  It is the standard measure quant funds
use to evaluate signals (Grinold & Kahn, "Active Portfolio Management"), it
is robust to outliers (rank-based), and it is transparent — a human can read
the number and understand exactly why a weight moved.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from . import config, db
from .factors import FACTOR_NAMES

log = logging.getLogger(__name__)


def _trading_days_between(d0: str, d1: str) -> int:
    return int(np.busday_count(d0, d1))


def update_outcomes(conn: sqlite3.Connection, latest_close: pd.Series,
                    today: str) -> int:
    """Record forward returns for scans that matured past any horizon.

    latest_close: Series symbol -> most recent close (from today's price pull).
    Returns number of (scan, horizon) evaluations added.
    """
    scans = db.list_scans(conn)
    added = 0
    for _, scan in scans.iterrows():
        elapsed = _trading_days_between(scan["scan_date"], today)
        for horizon in config.EVAL_HORIZONS_DAYS:
            if elapsed < horizon:
                continue
            done = conn.execute(
                "SELECT 1 FROM scan_eval WHERE scan_id=? AND horizon_days=?",
                (scan["scan_id"], horizon)).fetchone()
            if done:
                continue
            n = _evaluate(conn, int(scan["scan_id"]), horizon, elapsed, latest_close)
            if n:
                added += 1
                log.info("Evaluated scan %s at %dd horizon (%d stocks)",
                         scan["scan_date"], horizon, n)
    conn.commit()
    return added


def _evaluate(conn, scan_id: int, horizon: int, elapsed: int,
              latest_close: pd.Series) -> int:
    scores = db.get_scores(conn, scan_id)
    if scores.empty:
        return 0
    scores = scores.set_index("symbol")
    now = latest_close.reindex(scores.index)

    # Survivorship guard: a stock that vanished from the data (delisting,
    # ticker death) must NOT silently drop out of the evaluation — that would
    # flatter the track record by deleting the failures.  Mark it at its most
    # recent recorded close from any later scan (0% from its last sighting).
    missing = now[now.isna()].index.tolist()
    if missing:
        ph = ",".join("?" * len(missing))
        last_known = pd.read_sql_query(
            f"SELECT symbol, close FROM scores s JOIN scans c USING (scan_id) "
            f"WHERE symbol IN ({ph}) ORDER BY c.scan_date", conn, params=missing)
        last_known = last_known.drop_duplicates("symbol", keep="last").set_index("symbol")["close"]
        now = now.combine_first(last_known)
        log.info("Scan %d @%dd: %d symbols missing from live data — marked at "
                 "last recorded price (survivorship guard)", scan_id, horizon, len(missing))

    fwd = now / scores["close"] - 1.0
    fwd = fwd.replace([np.inf, -np.inf], np.nan).dropna()
    if len(fwd) < 50:
        return 0

    out_rows = [(scan_id, sym, horizon, elapsed, float(r)) for sym, r in fwd.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO outcomes (scan_id, symbol, horizon_days, elapsed_days,"
        " fwd_return) VALUES (?,?,?,?,?)", out_rows)

    # Top decile (full factor coverage only) vs whole scored universe
    full = scores[scores["n_factors"] == 4]
    n_top = max(10, int(len(full) * config.TOP_DECILE))
    top_syms = full.nsmallest(n_top, "rank").index
    top_ret = fwd.reindex(top_syms).mean()
    med_ret = fwd.median()

    # Bootstrap: how often would n_top stocks drawn AT RANDOM from the same
    # pool have done as well?  p ≈ 0.5 means the ranking added nothing;
    # a persistently small p is what genuine skill looks like.  Seeded for
    # reproducibility — the number is part of the permanent record.
    pool = fwd.reindex(full.index).dropna().to_numpy()
    p_value = None
    if len(pool) > n_top * 2:
        rng = np.random.default_rng(scan_id * 7919 + horizon)
        draws = rng.choice(pool, size=(2000, n_top), replace=True).mean(axis=1)
        p_value = float((draws >= top_ret).mean())

    conn.execute(
        "INSERT OR REPLACE INTO scan_eval (scan_id, horizon_days,"
        " top_decile_return, universe_median_return, spread, n_top,"
        " n_universe, p_value) VALUES (?,?,?,?,?,?,?,?)",
        (scan_id, horizon, float(top_ret), float(med_ret),
         float(top_ret - med_ret), int(len(top_syms)), int(len(fwd)), p_value))

    # Factor ICs (Spearman rank correlation, the industry-standard signal test)
    joined = scores.join(fwd.rename("fwd"), how="inner")
    for f in FACTOR_NAMES:
        col = f"{f}_score"
        sub = joined[[col, "fwd"]].dropna()
        ic = float(sub[col].corr(sub["fwd"], method="spearman")) if len(sub) > 50 else None
        conn.execute("INSERT OR REPLACE INTO factor_ic VALUES (?,?,?,?)",
                     (scan_id, horizon, f, ic))
    return len(fwd)


def _independent_eval_count(conn: sqlite3.Connection) -> int:
    """Number of NON-OVERLAPPING matured evaluation windows at the primary
    horizon.  Daily scans overlap almost entirely (the same quarter observed
    many times); counting them all would let the weights 'learn' from what is
    statistically one observation.  Greedy selection: take the earliest
    evaluated scan, then the next one at least PRIMARY_HORIZON trading days
    later, and so on.
    """
    dates = pd.read_sql_query(
        "SELECT DISTINCT s.scan_date FROM factor_ic f JOIN scans s USING (scan_id) "
        "WHERE f.horizon_days=? AND f.ic IS NOT NULL ORDER BY s.scan_date",
        conn, params=(config.PRIMARY_HORIZON,))["scan_date"].tolist()
    count, last = 0, None
    for d in dates:
        if last is None or _trading_days_between(last, d) >= config.PRIMARY_HORIZON:
            count += 1
            last = d
    return count


def maybe_update_weights(conn: sqlite3.Connection) -> dict[str, float] | None:
    """Adapt factor weights if enough INDEPENDENT evidence has accumulated.

    Returns the new weights if they changed, else None.
    """
    ics = pd.read_sql_query(
        "SELECT scan_id, factor, ic FROM factor_ic WHERE horizon_days=? AND ic IS NOT NULL",
        conn, params=(config.PRIMARY_HORIZON,))
    n_indep = _independent_eval_count(conn)
    if n_indep < config.MIN_INDEPENDENT_EVALS:
        log.info("Learning: %d/%d independent (non-overlapping) evaluations at "
                 "%dd horizon — weights unchanged",
                 n_indep, config.MIN_INDEPENDENT_EVALS, config.PRIMARY_HORIZON)
        return None

    # Point estimate still uses ALL matured scans (more data smooths noise);
    # only the trigger requires independence.
    mean_ic = ics.groupby("factor")["ic"].mean()
    current = db.get_current_weights(conn)

    # Target weights proportional to demonstrated predictive power (negative
    # IC contributes nothing but the floor keeps the factor alive in case the
    # regime changes back).
    target = {f: max(float(mean_ic.get(f, 0.0)), 0.0) for f in FACTOR_NAMES}
    if sum(target.values()) == 0:
        target = dict(config.DEFAULT_WEIGHTS)
    tot = sum(target.values())
    target = {f: v / tot for f, v in target.items()}

    lr = config.WEIGHT_LEARNING_RATE
    new = {f: (1 - lr) * current.get(f, 0.0) + lr * target[f] for f in FACTOR_NAMES}
    new = {f: max(v, config.WEIGHT_FLOOR) for f, v in new.items()}
    tot = sum(new.values())
    new = {f: round(v / tot, 4) for f, v in new.items()}

    if all(abs(new[f] - current.get(f, 0)) < 0.005 for f in FACTOR_NAMES):
        return None

    reason = (f"IC-based update over {ics['scan_id'].nunique()} matured scans "
              f"({n_indep} independent windows) at "
              f"{config.PRIMARY_HORIZON}d horizon. Mean ICs: "
              + ", ".join(f"{f}={mean_ic.get(f, float('nan')):.4f}" for f in FACTOR_NAMES))
    db.set_weights(conn, new, date.today().isoformat(), reason)
    log.info("Weights updated: %s (%s)", new, reason)
    return new


def track_record(conn: sqlite3.Connection) -> pd.DataFrame:
    """All matured scan evaluations joined with scan dates, for reporting."""
    return pd.read_sql_query(
        "SELECT s.scan_date, e.* FROM scan_eval e JOIN scans s USING (scan_id) "
        "ORDER BY s.scan_date, e.horizon_days", conn)
