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

from . import config

SCORE_COLS = [
    "symbol", "name", "exchange", "sector", "close", "market_cap", "dollar_volume",
    "composite", "rank", "n_factors",
    "value_score", "quality_score", "momentum_score", "low_vol_score",
    "earnings_yield", "fcf_yield", "book_to_market", "issuance",
    "gross_profitability", "roa", "leverage", "accruals",
    "asset_growth", "insider_net_mcap",
    "mom_12_1", "mom_6", "volatility", "revenue_growth",
]

TEXT_COLS = {"symbol", "name", "exchange", "sector"}

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
    ):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in wanted.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()


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
              notes: str = "") -> int:
    """Insert a scan and its scores.  Re-running the same day replaces it."""
    old = conn.execute("SELECT scan_id FROM scans WHERE scan_date=?", (scan_date,)).fetchone()
    if old:
        for table in ("scores", "outcomes", "scan_eval", "factor_ic", "scans"):
            conn.execute(f"DELETE FROM {table} WHERE scan_id=?", (old[0],))

    full_cov = int((scored["n_factors"] == 4).sum())
    cur = conn.execute(
        "INSERT INTO scans (scan_date, universe_size, scored_size, full_coverage,"
        " weights_json, notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (scan_date, universe_size, len(scored), full_cov,
         json.dumps(weights), notes, datetime.now().isoformat(timespec="seconds")),
    )
    scan_id = cur.lastrowid

    rows = scored[SCORE_COLS].copy()
    rows.insert(0, "scan_id", scan_id)
    rows.to_sql("scores", conn, if_exists="append", index=False)
    conn.commit()
    return scan_id


def list_scans(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM scans ORDER BY scan_date", conn)


def get_scores(conn: sqlite3.Connection, scan_id: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM scores WHERE scan_id=? ORDER BY rank", conn, params=(scan_id,))
