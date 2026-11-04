"""Permanent record-keeping in SQLite.

Every scan, every score, every realised outcome and every weight change is
stored forever — that history is what lets the system (and its users) judge
whether the screener actually has predictive power.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd

from . import config, factors

SCORE_COLS = [
    "symbol", "name", "exchange", "sector", "reporting_standard",
    "close", "market_cap", "dollar_volume",
    "composite", "rank", "n_factors", "n_metrics", "eligible",
    "value_score", "quality_score", "momentum_score", "low_vol_score",
    "earnings_yield", "fcf_yield", "book_to_market", "issuance",
    "gross_profitability", "roa", "leverage", "accruals",
    "asset_growth", "insider_net_mcap",
    "mom_12_1", "mom_6", "volatility", "revenue_growth",
]

TEXT_COLS = {"symbol", "name", "exchange", "sector", "reporting_standard"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date     TEXT UNIQUE NOT NULL,
    universe_size INTEGER,
    scored_size   INTEGER,
    full_coverage INTEGER,
    weights_json  TEXT,
    notes         TEXT,
    created_at    TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    scan_id INTEGER NOT NULL,
    {score_cols},
    PRIMARY KEY (scan_id, symbol)
);
CREATE TABLE IF NOT EXISTS outcomes (
    scan_id      INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    elapsed_days INTEGER,
    fwd_return   REAL,
    PRIMARY KEY (scan_id, symbol, horizon_days)
);
CREATE TABLE IF NOT EXISTS scan_eval (
    scan_id                 INTEGER NOT NULL,
    horizon_days            INTEGER NOT NULL,
    top_decile_return       REAL,
    universe_median_return  REAL,
    spread                  REAL,
    n_top                   INTEGER,
    n_universe              INTEGER,
    p_value                 REAL,
    PRIMARY KEY (scan_id, horizon_days)
);
CREATE TABLE IF NOT EXISTS factor_ic (
    scan_id      INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    factor       TEXT NOT NULL,
    ic           REAL,
    PRIMARY KEY (scan_id, horizon_days, factor)
);
-- Dividends and splits (2026-11 review, proposals A and B).  Filled from
-- every scan's price download; the permanent source for total-return
-- outcomes, book dividend credits, SPY total return and split-adjusted
-- share issuance.  See screener/data/prices.corporate_actions().
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol  TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    kind    TEXT NOT NULL,
    amount  REAL,
    factor  REAL,
    PRIMARY KEY (symbol, ex_date, kind)
);
CREATE TABLE IF NOT EXISTS weights_history (
    effective_date TEXT NOT NULL,
    weights_json   TEXT NOT NULL,
    reason         TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    col_defs = ",\n    ".join(
        f'{c} {"TEXT" if c in TEXT_COLS else "REAL"}'
        + (" NOT NULL" if c == "symbol" else "")
        for c in SCORE_COLS
    )
    conn.executescript(SCHEMA.format(score_cols=col_defs))
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any columns the running code expects that an older DB lacks —
    history is never dropped, the schema grows around it."""
    for table, wanted in (
        ("scores", {c: ("TEXT" if c in TEXT_COLS else "REAL") for c in SCORE_COLS}),
        ("scan_eval", {"p_value": "REAL"}),
        # price_asof: the trading date the scan's prices are actually from.
        # Equal to scan_date for every scan since v0.3; for older scans it
        # was measured against the price history (2026-11-05), because cloud
        # runs between ~00:00 and ~01:40 UTC received the PREVIOUS day's bars.
        ("scans", {"spy_close": "REAL", "price_asof": "TEXT"}),
        ("outcomes", {"price_return": "REAL"}),
    ):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in wanted.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()


def save_corporate_actions(conn: sqlite3.Connection, ev: pd.DataFrame) -> int:
    """Upsert dividend/split events.  A later download may fill a factor an
    earlier one could not compute, but never blanks one."""
    if ev is None or ev.empty:
        return 0
    conn.executemany(
        "INSERT INTO corporate_actions (symbol, ex_date, kind, amount, factor)"
        " VALUES (?,?,?,?,?) ON CONFLICT(symbol, ex_date, kind) DO UPDATE SET"
        " amount=excluded.amount, factor=COALESCE(excluded.factor, factor)",
        [(r.symbol, r.ex_date, r.kind, r.amount,
          None if r.factor is None or r.factor != r.factor else float(r.factor))
         for r in ev.itertuples(index=False)])
    conn.commit()
    return len(ev)


def action_factors(conn: sqlite3.Connection, start: str, end: str,
                   symbols=None) -> pd.Series:
    """symbol -> product of total-return factors (dividends reinvested,
    splits applied) for events with start < ex_date <= end.  Multiplying a
    price-only return's gross value by this gives the total return; symbols
    with no events are absent (factor 1)."""
    q = ("SELECT symbol, factor FROM corporate_actions "
         "WHERE ex_date > ? AND ex_date <= ? AND factor IS NOT NULL")
    ev = pd.read_sql_query(q, conn, params=(start, end))
    if symbols is not None:
        ev = ev[ev["symbol"].isin(set(symbols))]
    if ev.empty:
        return pd.Series(dtype=float)
    return ev.groupby("symbol")["factor"].prod()


def get_current_weights(conn: sqlite3.Connection) -> dict[str, float]:
    row = conn.execute(
        "SELECT weights_json FROM weights_history ORDER BY effective_date DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row:
        return json.loads(row[0])
    return dict(config.DEFAULT_WEIGHTS)


def set_weights(conn: sqlite3.Connection, weights: dict[str, float],
                effective_date: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO weights_history (effective_date, weights_json, reason) VALUES (?,?,?)",
        (effective_date, json.dumps(weights), reason),
    )
    conn.commit()


def save_scan(conn: sqlite3.Connection, scan_date: str, universe_size: int,
              scored: pd.DataFrame, weights: dict[str, float],
              notes: str = "", spy_close: float | None = None) -> int:
    """Insert a scan and its scores.  A second scan of the same price date
    replaces the first, which is ARCHIVED to scans_superseded /
    scores_superseded rather than deleted (its scores may already have driven
    book trades)."""
    old = conn.execute("SELECT scan_id FROM scans WHERE scan_date=?", (scan_date,)).fetchone()
    if old:
        _archive_scan(conn, old[0], f"replaced by a later scan of price date "
                                    f"{scan_date} ({datetime.now():%Y-%m-%d %H:%M})")

    full_cov = int(factors.eligible_mask(scored).sum())
    cur = conn.execute(
        "INSERT INTO scans (scan_date, universe_size, scored_size, full_coverage,"
        " weights_json, notes, created_at, spy_close, price_asof)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (scan_date, universe_size, len(scored), full_cov,
         json.dumps(weights), notes, datetime.now().isoformat(timespec="seconds"),
         spy_close, scan_date),
    )
    scan_id = cur.lastrowid

    rows = scored[SCORE_COLS].copy()
    rows.insert(0, "scan_id", scan_id)
    rows.to_sql("scores", conn, if_exists="append", index=False)
    conn.commit()
    return scan_id


def _archive_scan(conn: sqlite3.Connection, scan_id: int, reason: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS scans_superseded AS "
                 "SELECT *, '' AS reason FROM scans WHERE 0")
    conn.execute("CREATE TABLE IF NOT EXISTS scores_superseded AS "
                 "SELECT * FROM scores WHERE 0")
    for arch, src in (("scans_superseded", "scans"), ("scores_superseded", "scores")):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({arch})")}
        for col, typ in ((r[1], r[2]) for r in conn.execute(f"PRAGMA table_info({src})")):
            if col not in have:
                conn.execute(f"ALTER TABLE {arch} ADD COLUMN {col} {typ}")
    scols = [r[1] for r in conn.execute("PRAGMA table_info(scans)")]
    conn.execute(f"INSERT INTO scans_superseded ({','.join(scols)}, reason) "
                 f"SELECT {','.join(scols)}, ? FROM scans WHERE scan_id=?",
                 (reason, scan_id))
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(scores)")]
    conn.execute(f"INSERT INTO scores_superseded ({','.join(ccols)}) "
                 f"SELECT {','.join(ccols)} FROM scores WHERE scan_id=?", (scan_id,))
    for table in ("scores", "outcomes", "scan_eval", "factor_ic", "scans"):
        conn.execute(f"DELETE FROM {table} WHERE scan_id=?", (scan_id,))


def list_scans(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM scans ORDER BY scan_date", conn)


def get_scores(conn: sqlite3.Connection, scan_id: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM scores WHERE scan_id=? ORDER BY rank", conn, params=(scan_id,))
