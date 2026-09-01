"""Claude's Picks — the live hypothetical portfolio experiment.

A $10,000 paper portfolio deployed into the screener's top-ranked stocks and
tracked daily against buy-and-hold SPY. The point is not the money (there is
none); it is accountability: the screener's rankings are abstract until they
are forced to live inside a portfolio with fees, concentration and drawdowns.

Strategy (documented fully in docs/PORTFOLIO_EXPERIMENT.md):
  * Equal-weight the top 10 composite-ranked stocks with full factor coverage.
  * Whole shares only; residual stays as cash.
  * IBKR Fixed commission model: $0.005/share, min $1, max 1% of trade value.
  * Monthly review (--review): any holding whose composite rank has decayed
    beyond MAX_HELD_RANK is sold and replaced by the best-ranked stock not
    already held.  No other trading — no stops, no daily churn.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

import pandas as pd

from . import config, db

log = logging.getLogger(__name__)

START_CASH = 10_000.00
N_POSITIONS = 10
MAX_HELD_RANK = 50      # sell at review if rank (among full-coverage) decays past this
MAX_PER_SECTOR = 2      # selection cap so the book is stock picks, not one sector bet

SCHEMA = """
CREATE TABLE IF NOT EXISTS pf_txns (
    txn_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    date    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    side    TEXT NOT NULL,            -- BUY / SELL
    shares  REAL NOT NULL,
    price   REAL NOT NULL,
    fee     REAL NOT NULL,
    note    TEXT
);
CREATE TABLE IF NOT EXISTS pf_snapshots (
    date            TEXT PRIMARY KEY,
    positions_value REAL,
    cash            REAL,
    total           REAL,
    spy_close       REAL
);
"""


def ibkr_fee(shares: float, price: float) -> float:
    """IBKR Fixed pricing, US stocks: $0.005/share, min $1, max 1% of value."""
    return round(min(max(1.0, 0.005 * shares), 0.01 * shares * price), 2)


def connect() -> sqlite3.Connection:
    conn = db.connect()
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def holdings(conn) -> pd.DataFrame:
    """Net position per symbol with average cost (fees capitalised into cost)."""
    tx = pd.read_sql_query("SELECT * FROM pf_txns ORDER BY txn_id", conn)
    if tx.empty:
        return pd.DataFrame(columns=["symbol", "shares", "cost_basis"])
    rows = {}
    for _, t in tx.iterrows():
        h = rows.setdefault(t["symbol"], {"shares": 0.0, "cost": 0.0})
        if t["side"] == "BUY":
            h["cost"] += t["shares"] * t["price"] + t["fee"]
            h["shares"] += t["shares"]
        else:
            if h["shares"] > 0:  # reduce cost proportionally on sells
                h["cost"] *= (h["shares"] - t["shares"]) / h["shares"]
            h["shares"] -= t["shares"]
    out = pd.DataFrame(
        [{"symbol": s, "shares": v["shares"], "cost_basis": round(v["cost"], 2)}
         for s, v in rows.items() if v["shares"] > 1e-9])
    return out


def cash(conn) -> float:
    tx = pd.read_sql_query("SELECT side, shares, price, fee FROM pf_txns", conn)
    c = START_CASH
    for _, t in tx.iterrows():
        flow = t["shares"] * t["price"]
        c += (flow - t["fee"]) if t["side"] == "SELL" else -(flow + t["fee"])
    return round(c, 2)


def _record(conn, day: str, symbol: str, side: str, shares: float,
            price: float, note: str) -> None:
    fee = ibkr_fee(shares, price)
    conn.execute(
        "INSERT INTO pf_txns (date, symbol, side, shares, price, fee, note) "
        "VALUES (?,?,?,?,?,?,?)", (day, symbol, side, shares, price, fee, note))
    log.info("%s %s %g @ $%.2f (fee $%.2f) — %s", side, symbol, shares, price, fee, note)


# ---------------------------------------------------------------------------
# Latest scan helpers
# ---------------------------------------------------------------------------

def latest_scan(conn) -> tuple[int, str, pd.DataFrame]:
    row = conn.execute(
        "SELECT scan_id, scan_date FROM scans ORDER BY scan_date DESC, scan_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("No scans in database — run run_scan.py first.")
    scores = db.get_scores(conn, row[0])
    return row[0], row[1], scores


def full_coverage_ranked(scores: pd.DataFrame) -> pd.DataFrame:
    """Full-coverage stocks re-ranked 1..N by composite (the pickable set)."""
    full = scores[scores["n_factors"] == 4].copy()
    full = full.sort_values("composite", ascending=False).reset_index(drop=True)
    full["fc_rank"] = full.index + 1
    return full


def spy_close() -> float | None:
    try:
        import yfinance as yf
        h = yf.download("SPY", period="10d", auto_adjust=True, progress=False)
        if h is not None and not h.empty:
            v = h["Close"].iloc[-1]
            return round(float(v.iloc[0] if hasattr(v, "iloc") else v), 2)
    except Exception as e:
        log.warning("SPY fetch failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _select_diversified(ranked: pd.DataFrame, n: int,
                        sector_counts: dict[str, int] | None = None) -> pd.DataFrame:
    """Walk down the ranking, skipping stocks whose sector is already at the
    MAX_PER_SECTOR cap (added 2026-09-01 with sector-aware scoring)."""
    counts: dict[str, int] = dict(sector_counts or {})
    rows = []
    for _, p in ranked.iterrows():
        sec = p.get("sector") or "Other"
        if counts.get(sec, 0) >= MAX_PER_SECTOR:
            continue
        counts[sec] = counts.get(sec, 0) + 1
        rows.append(p)
        if len(rows) >= n:
            break
    return pd.DataFrame(rows)


def init_portfolio(conn) -> None:
    if not holdings(conn).empty:
        raise RuntimeError("Portfolio already initialised — refusing to re-seed.")
    _, day, scores = latest_scan(conn)
    picks = _select_diversified(full_coverage_ranked(scores), N_POSITIONS)
    slice_cash = START_CASH / N_POSITIONS
    for _, p in picks.iterrows():
        shares = int(slice_cash // p["close"])
        if shares <= 0:
            continue
        _record(conn, day, p["symbol"], "BUY", shares, p["close"],
                f"initial deployment — composite rank #{p['fc_rank']}")
    conn.commit()


def review(conn) -> list[str]:
    """Monthly review: replace holdings whose rank decayed past MAX_HELD_RANK."""
    _, day, scores = latest_scan(conn)
    ranked = full_coverage_ranked(scores).set_index("symbol")
    held = holdings(conn)
    actions = []
    for _, h in held.iterrows():
        sym = h["symbol"]
        rank = int(ranked.at[sym, "fc_rank"]) if sym in ranked.index else None
        if rank is None or rank > MAX_HELD_RANK:
            px = float(ranked.at[sym, "close"]) if sym in ranked.index else None
            if px is None:
                px_row = scores[scores["symbol"] == sym]
                px = float(px_row["close"].iloc[0]) if not px_row.empty else None
            if px is None:
                actions.append(f"HOLD {sym}: no price in latest scan — manual review needed")
                continue
            _record(conn, day, sym, "SELL", h["shares"], px,
                    f"review: rank decayed to {rank or 'unranked'} (> {MAX_HELD_RANK})")
            # replacement: best-ranked stock not held, respecting the sector cap
            still_held = set(holdings(conn)["symbol"])
            sector_counts: dict[str, int] = {}
            for hs in still_held:
                sec = ranked.at[hs, "sector"] if hs in ranked.index else "Other"
                sector_counts[sec or "Other"] = sector_counts.get(sec or "Other", 0) + 1
            candidates = ranked[~ranked.index.isin(still_held)].reset_index()
            repl = _select_diversified(candidates, 1, sector_counts)
            if not repl.empty:
                r = repl.iloc[0]
                budget = min(cash(conn), START_CASH / N_POSITIONS * 1.2)
                shares = int(budget // r["close"])
                if shares > 0:
                    _record(conn, day, r["symbol"], "BUY", shares, r["close"],
                            f"review: replacement, composite rank #{int(r['fc_rank'])}")
                    actions.append(f"SWAP {sym} -> {r['symbol']}")
    conn.commit()
    return actions


def snapshot(conn) -> dict:
    """Mark the portfolio to market off the latest scan; store daily snapshot."""
    _, day, scores = latest_scan(conn)
    px = scores.set_index("symbol")["close"]
    held = holdings(conn)
    pos_val = 0.0
    for _, h in held.iterrows():
        p = px.get(h["symbol"])
        if p is None or pd.isna(p):
            last = conn.execute(
                "SELECT price FROM pf_txns WHERE symbol=? ORDER BY txn_id DESC LIMIT 1",
                (h["symbol"],)).fetchone()
            p = last[0] if last else 0.0
            log.warning("%s missing from latest scan — using last known price", h["symbol"])
        pos_val += h["shares"] * float(p)
    c = cash(conn)
    spy = spy_close()
    conn.execute("INSERT OR REPLACE INTO pf_snapshots VALUES (?,?,?,?,?)",
                 (day, round(pos_val, 2), c, round(pos_val + c, 2), spy))
    conn.commit()
    return {"date": day, "positions_value": round(pos_val, 2), "cash": c,
            "total": round(pos_val + c, 2), "spy_close": spy}


def position_table(conn) -> pd.DataFrame:
    _, _, scores = latest_scan(conn)
    ranked = full_coverage_ranked(scores).set_index("symbol")
    px = scores.set_index("symbol")["close"]
    held = holdings(conn)
    rows = []
    for _, h in held.iterrows():
        sym = h["symbol"]
        p = float(px.get(sym, float("nan")))
        val = h["shares"] * p
        rows.append({
            "symbol": sym,
            "shares": h["shares"],
            "cost_basis": h["cost_basis"],
            "price": round(p, 2),
            "value": round(val, 2),
            "pnl": round(val - h["cost_basis"], 2),
            "pnl_pct": round((val / h["cost_basis"] - 1) * 100, 2) if h["cost_basis"] else None,
            "rank_now": int(ranked.at[sym, "fc_rank"]) if sym in ranked.index else None,
        })
    return pd.DataFrame(rows).sort_values("value", ascending=False)
