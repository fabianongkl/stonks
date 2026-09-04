"""The hypothetical portfolio experiment — now TWO books, one ledger.

  core        "Claude's Picks" — $10,000, 10 positions, the scan's own
              composite, patient rules.  The balanced reference book.
  aggressive  "Hyper-Aggressive" — $100,000, 8 concentrated positions,
              momentum-dominant weighting (0.50/0.20/0.20/0.10 across
              momentum/low-vol/quality/value), faster rotation (rank
              tolerance 100 on ITS OWN ranking).  This tilt was openly
              informed by the 5-year backtest's factor ICs — which makes
              this book a live A/B test of "follow the backtest" against
              the core book's "trust the priors".  The live record judges.

Neither book uses leverage or options: simulating margin calls and IV
without real borrow/options data would be fantasy math on the permanent
record.  Aggression here means concentration, tilt and turnover.

Fees: IBKR Fixed commission model on every trade.
Rules documented in docs/PORTFOLIO_EXPERIMENT.md.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from . import db
from .factors import FACTOR_NAMES

log = logging.getLogger(__name__)

MAX_PER_SECTOR = 2      # selection cap for every book

BOOKS: dict[str, dict] = {
    "core": {
        "label": "Claude's Picks",
        "start_cash": 10_000.0,
        "n_positions": 10,
        "max_held_rank": 50,
        "weights": None,          # None -> the scan's stored composite
        "page": "portfolio.html",
    },
    "aggressive": {
        "label": "Hyper-Aggressive",
        "start_cash": 100_000.0,
        "n_positions": 8,
        "max_held_rank": 100,
        "weights": {"momentum": 0.50, "low_vol": 0.20,
                    "quality": 0.20, "value": 0.10},
        "page": "aggressive.html",
    },
    # The user's "Top-3 ritual", run live with zero survivorship bias:
    # each January, sell everything and buy the prior calendar year's three
    # biggest S&P 500 gainers, equal-weight. No factor scores, no sector
    # cap, no monthly reviews — one mechanical decision a year.  Inception
    # (2026-09) uses the trailing 12 months; first true rotation Jan 2027.
    "ritual": {
        "label": "Top-3 Ritual",
        "start_cash": 10_000.0,
        "n_positions": 3,
        "max_held_rank": None,
        "weights": None,
        "style": "ritual",
        "page": "ritual.html",
    },
    # Mirror of the ritual: each January buy the prior calendar year's THREE
    # WORST S&P performers (De Bondt & Thaler reversal).  Together the two
    # rituals turn "do winners persist?" into a real experiment.
    "reversal": {
        "label": "Loser Reversal",
        "start_cash": 10_000.0,
        "n_positions": 3,
        "max_held_rank": None,
        "weights": None,
        "style": "ritual",
        "worst": True,
        "page": "reversal.html",
    },
    # The control group: 10 stocks drawn at RANDOM from the same
    # full-coverage pool the factor books pick from, held for a year,
    # redrawn each January.  Seeded by the year (rng seed = year*7919+101)
    # so anyone can reproduce the draw.  Any book that can't beat the
    # monkey has no business claiming skill.
    "monkey": {
        "label": "Monkey Control",
        "start_cash": 10_000.0,
        "n_positions": 10,
        "max_held_rank": None,
        "weights": None,
        "style": "monkey",
        "page": "monkey.html",
    },
    # Follow the insiders: the 8 stocks with the largest net insider
    # open-market BUYING scaled by market cap (SEC Form 4 data, 1-2
    # quarters stale by publication), market cap >= $250M, rotated on the
    # first scan of each calendar quarter.  Evidence: Seyhun 1986;
    # Lakonishok & Lee 2001.
    "insider": {
        "label": "Insider Buying",
        "start_cash": 10_000.0,
        "n_positions": 8,
        "max_held_rank": None,
        "weights": None,
        "style": "insider",
        "page": "insider.html",
    },
}

INSIDER_MIN_MCAP = 250e6

# kept for older callers/docs
START_CASH = BOOKS["core"]["start_cash"]
N_POSITIONS = BOOKS["core"]["n_positions"]
MAX_HELD_RANK = BOOKS["core"]["max_held_rank"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS pf_txns (
    txn_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    date    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    side    TEXT NOT NULL,
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
    _migrate_books(conn)
    return conn


def _migrate_books(conn: sqlite3.Connection) -> None:
    """Add the `book` dimension to ledger tables created before it existed."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pf_txns)")}
    if "book" not in cols:
        conn.execute("ALTER TABLE pf_txns ADD COLUMN book TEXT NOT NULL DEFAULT 'core'")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pf_snapshots)")}
    if "book" not in cols:
        conn.executescript("""
            CREATE TABLE pf_snapshots_v2 (
                date TEXT NOT NULL, book TEXT NOT NULL,
                positions_value REAL, cash REAL, total REAL, spy_close REAL,
                PRIMARY KEY (date, book)
            );
            INSERT INTO pf_snapshots_v2
                SELECT date, 'core', positions_value, cash, total, spy_close
                FROM pf_snapshots;
            DROP TABLE pf_snapshots;
            ALTER TABLE pf_snapshots_v2 RENAME TO pf_snapshots;
        """)
    conn.commit()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def holdings(conn, book: str = "core") -> pd.DataFrame:
    tx = pd.read_sql_query(
        "SELECT * FROM pf_txns WHERE book=? ORDER BY txn_id", conn, params=(book,))
    if tx.empty:
        return pd.DataFrame(columns=["symbol", "shares", "cost_basis"])
    rows: dict[str, dict] = {}
    for _, t in tx.iterrows():
        h = rows.setdefault(t["symbol"], {"shares": 0.0, "cost": 0.0})
        if t["side"] == "BUY":
            h["cost"] += t["shares"] * t["price"] + t["fee"]
            h["shares"] += t["shares"]
        else:
            if h["shares"] > 0:
                h["cost"] *= (h["shares"] - t["shares"]) / h["shares"]
            h["shares"] -= t["shares"]
    return pd.DataFrame(
        [{"symbol": s, "shares": v["shares"], "cost_basis": round(v["cost"], 2)}
         for s, v in rows.items() if v["shares"] > 1e-9])


def cash(conn, book: str = "core") -> float:
    tx = pd.read_sql_query(
        "SELECT side, shares, price, fee FROM pf_txns WHERE book=?",
        conn, params=(book,))
    c = BOOKS[book]["start_cash"]
    for _, t in tx.iterrows():
        flow = t["shares"] * t["price"]
        c += (flow - t["fee"]) if t["side"] == "SELL" else -(flow + t["fee"])
    return round(c, 2)


def _record(conn, book: str, day: str, symbol: str, side: str, shares: float,
            price: float, note: str) -> None:
    fee = ibkr_fee(shares, price)
    conn.execute(
        "INSERT INTO pf_txns (book, date, symbol, side, shares, price, fee, note)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (book, day, symbol, side, shares, price, fee, note))
    log.info("[%s] %s %s %g @ $%.2f (fee $%.2f) — %s",
             book, side, symbol, shares, price, fee, note)


# ---------------------------------------------------------------------------
# Latest scan / ranking per book
# ---------------------------------------------------------------------------

def latest_scan(conn) -> tuple[int, str, pd.DataFrame]:
    row = conn.execute(
        "SELECT scan_id, scan_date FROM scans ORDER BY scan_date DESC, scan_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("No scans in database — run run_scan.py first.")
    return row[0], row[1], db.get_scores(conn, row[0])


def _sp500_symbols() -> set[str]:
    from .data import sp500
    try:
        return set(sp500.fetch_members()["symbol"])
    except Exception as e:
        log.warning("S&P membership fetch failed: %s", e)
        return set()


def book_ranked(scores: pd.DataFrame, book: str) -> pd.DataFrame:
    """Stocks ranked 1..N by THIS book's scoring (informational between
    rotations for the auto-rotating styles)."""
    style = BOOKS[book].get("style")
    if style == "ritual":
        # trailing 12-month momentum among S&P members; reversal book ranks
        # from the bottom (its "best" pick is the worst performer)
        mem = _sp500_symbols()
        sp = scores[scores["symbol"].isin(mem)].copy()
        asc = bool(BOOKS[book].get("worst"))
        sp = sp.sort_values("mom_12_1", ascending=asc).reset_index(drop=True)
        sp["book_score"] = sp["mom_12_1"]
        sp["fc_rank"] = sp.index + 1
        return sp
    if style == "insider":
        sub = scores[(scores["market_cap"] >= INSIDER_MIN_MCAP)
                     & (scores["insider_net_mcap"].notna())].copy()
        sub = sub.sort_values("insider_net_mcap", ascending=False).reset_index(drop=True)
        sub["book_score"] = sub["insider_net_mcap"]
        sub["fc_rank"] = sub.index + 1
        return sub
    # monkey ranks informationally by the screener composite — showing what
    # the model thinks of the random draw is half the fun
    full = scores[scores["n_factors"] == 4].copy()
    w = BOOKS[book]["weights"]
    if w is None:
        full["book_score"] = full["composite"]
    else:
        fs = full[[f"{f}_score" for f in FACTOR_NAMES]]
        ws = pd.Series({f"{f}_score": w.get(f, 0.0) for f in FACTOR_NAMES})
        avail = fs.notna()
        wsum = avail.mul(ws, axis=1).sum(axis=1)
        full["book_score"] = (fs.fillna(0).mul(ws, axis=1).sum(axis=1)
                              / wsum.replace(0, np.nan))
    full = full.sort_values("book_score", ascending=False).reset_index(drop=True)
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


def _ritual_top3(scores: pd.DataFrame, calendar_year: int | None = None,
                 worst: bool = False) -> pd.DataFrame:
    """Ritual picks: top (or, for the reversal book, BOTTOM) 3 S&P members
    by formation return.

    calendar_year=None -> trailing 12 months (inception); otherwise the
    prior calendar year's return (the true January rule).  Formation windows
    must be fully covered by price data.
    """
    from .data import sp500
    members = sp500.fetch_members()
    hist = sp500.fetch_history(members["symbol"].tolist())
    C = hist.pivot_table(index="date", columns="symbol", values="close")
    C.index = pd.to_datetime(C.index)
    C = C.sort_index().ffill(limit=10)
    if calendar_year is not None:
        ye = C.groupby(C.index.year).tail(1)
        y_idx = {int(d.year): d for d in ye.index}
        if calendar_year not in y_idx or calendar_year - 1 not in y_idx:
            raise RuntimeError(f"No year-end prices for {calendar_year}")
        form = ye.loc[y_idx[calendar_year]] / ye.loc[y_idx[calendar_year - 1]] - 1
        window = C[(C.index.year == calendar_year)]
    else:
        form = C.iloc[-1] / C.iloc[-253] - 1
        window = C.iloc[-253:]
    coverage = window.notna().mean()
    ok = form.dropna().index[(coverage[form.dropna().index] > 0.9)]
    ok = [s for s in ok if s != "SPY"]
    top = form[ok].nsmallest(3) if worst else form[ok].nlargest(3)
    px = scores.set_index("symbol")["close"]
    rows = [{"symbol": s, "formation": float(top[s]),
             "close": float(px.get(s, float("nan")))} for s in top.index]
    out = pd.DataFrame(rows).dropna(subset=["close"])
    if len(out) < 3:
        raise RuntimeError("Ritual picks missing prices in latest scan")
    return out


def _monkey_picks(scores: pd.DataFrame, year: int, n: int) -> pd.DataFrame:
    """n stocks drawn uniformly at random from the full-coverage pool.
    Seeded by the draw year so the pick is reproducible by anyone."""
    pool = sorted(scores[scores["n_factors"] == 4]["symbol"].tolist())
    rng = np.random.default_rng(year * 7919 + 101)
    chosen = list(rng.choice(pool, size=min(n, len(pool)), replace=False))
    px = scores.set_index("symbol")["close"]
    return pd.DataFrame([{"symbol": s, "formation": float("nan"),
                          "close": float(px[s])} for s in chosen])


def _insider_picks(scores: pd.DataFrame, n: int) -> pd.DataFrame:
    """Top n by net insider open-market buying / market cap."""
    sub = scores[(scores["market_cap"] >= INSIDER_MIN_MCAP)
                 & (scores["insider_net_mcap"] > 0)]
    sub = sub.sort_values("insider_net_mcap", ascending=False).head(n)
    if len(sub) < n:
        raise RuntimeError(f"Only {len(sub)} stocks with positive insider "
                           f"buying above the size floor")
    return pd.DataFrame([{"symbol": r["symbol"],
                          "formation": float(r["insider_net_mcap"]),
                          "close": float(r["close"])}
                         for _, r in sub.iterrows()])


def _buy_equal(conn, book: str, day: str, picks: pd.DataFrame,
               budget: float, note_of) -> None:
    """Equal-weight fractional-share deployment with fee headroom."""
    slice_cash = budget / len(picks) - 2.0
    for _, p in picks.iterrows():
        shares = round(slice_cash / p["close"], 4)
        if shares > 0:
            _record(conn, book, day, p["symbol"], "BUY", shares, p["close"],
                    note_of(p))
    conn.commit()


def _sell_all(conn, book: str, day: str, scores: pd.DataFrame,
              reason: str) -> list[str]:
    actions = []
    px = scores.set_index("symbol")["close"]
    for _, h in holdings(conn, book).iterrows():
        p = px.get(h["symbol"])
        if p is None or pd.isna(p):
            last = conn.execute(
                "SELECT price FROM pf_txns WHERE book=? AND symbol=? "
                "ORDER BY txn_id DESC LIMIT 1", (book, h["symbol"])).fetchone()
            p = last[0] if last else 0.0
        if p and p > 0:
            _record(conn, book, day, h["symbol"], "SELL", h["shares"], float(p),
                    reason)
            actions.append(f"SELL {h['symbol']}")
    return actions


def auto_rotate_if_due(conn) -> list[str]:
    """Mechanical rotations, checked daily and firing only when due:
    ritual/reversal/monkey rotate on the first scan of a new CALENDAR YEAR;
    the insider book on the first scan of a new CALENDAR QUARTER."""
    _, day, scores = latest_scan(conn)
    scan_year = int(day[:4])
    scan_q = (scan_year, (int(day[5:7]) - 1) // 3 + 1)
    all_actions = []
    for book, cfg in BOOKS.items():
        style = cfg.get("style")
        if style not in ("ritual", "monkey", "insider"):
            continue
        if holdings(conn, book).empty:
            continue
        last_buy = conn.execute(
            "SELECT MAX(date) FROM pf_txns WHERE book=? AND side='BUY'",
            (book,)).fetchone()[0]
        if not last_buy:
            continue
        if style == "insider":
            last_q = (int(last_buy[:4]), (int(last_buy[5:7]) - 1) // 3 + 1)
            due = scan_q > last_q
        else:
            due = int(last_buy[:4]) < scan_year
        if not due:
            continue
        acts = _sell_all(conn, book, day, scores,
                         f"{style}: scheduled rotation out")
        budget = cash(conn, book)
        if style == "ritual":
            picks = _ritual_top3(scores, calendar_year=scan_year - 1,
                                 worst=bool(cfg.get("worst")))
            kind = "loser" if cfg.get("worst") else "winner"
            note = (lambda p, k=kind, y=scan_year:
                    f"ritual: {y - 1} {k} ({p['formation']:+.0%}), held for {y}")
        elif style == "monkey":
            picks = _monkey_picks(scores, scan_year, cfg["n_positions"])
            note = (lambda p, y=scan_year:
                    f"monkey: random draw for {y} (seed {y}*7919+101)")
        else:
            picks = _insider_picks(scores, cfg["n_positions"])
            note = (lambda p:
                    f"insider: net buying {p['formation']:.2%} of mcap")
        _buy_equal(conn, book, day, picks, budget, note)
        all_actions += [f"[{book}] {a}" for a in acts]
        all_actions += [f"[{book}] BUY {p['symbol']}" for _, p in picks.iterrows()]
    return all_actions


def init_portfolio(conn, book: str = "core") -> None:
    if not holdings(conn, book).empty:
        raise RuntimeError(f"Book '{book}' already initialised — refusing to re-seed.")
    cfg = BOOKS[book]
    _, day, scores = latest_scan(conn)
    style = cfg.get("style")
    if style == "ritual":
        worst = bool(cfg.get("worst"))
        picks = _ritual_top3(scores, worst=worst)
        kind = "loser" if worst else "winner"
        _buy_equal(conn, book, day, picks, cfg["start_cash"],
                   lambda p, k=kind: f"inception: trailing-12m {k} "
                   f"({p['formation']:+.0%}); first calendar rotation "
                   f"Jan {int(day[:4]) + 1}")
        return
    if style == "monkey":
        picks = _monkey_picks(scores, int(day[:4]), cfg["n_positions"])
        _buy_equal(conn, book, day, picks, cfg["start_cash"],
                   lambda p, y=int(day[:4]):
                   f"inception: random draw (seed {y}*7919+101); redraw each Jan")
        return
    if style == "insider":
        picks = _insider_picks(scores, cfg["n_positions"])
        _buy_equal(conn, book, day, picks, cfg["start_cash"],
                   lambda p: f"inception: net insider buying "
                   f"{p['formation']:.2%} of mcap; quarterly rotation")
        return
    picks = _select_diversified(book_ranked(scores, book), cfg["n_positions"])
    slice_cash = cfg["start_cash"] / cfg["n_positions"]
    for _, p in picks.iterrows():
        shares = int(slice_cash // p["close"])
        if shares <= 0:
            continue
        _record(conn, book, day, p["symbol"], "BUY", shares, p["close"],
                f"initial deployment — {cfg['label']} rank #{int(p['fc_rank'])}")
    conn.commit()


def review(conn, book: str = "core") -> list[str]:
    """Monthly review: replace holdings whose book-rank decayed past tolerance.

    Auto-rotating books (ritual, reversal, monkey, insider) have no monthly
    rule — their schedules live in auto_rotate_if_due — so review is a
    deliberate no-op for them."""
    cfg = BOOKS[book]
    if cfg.get("style") in ("ritual", "monkey", "insider"):
        return []
    _, day, scores = latest_scan(conn)
    ranked = book_ranked(scores, book).set_index("symbol")
    held = holdings(conn, book)
    actions = []
    for _, h in held.iterrows():
        sym = h["symbol"]
        rank = int(ranked.at[sym, "fc_rank"]) if sym in ranked.index else None
        if rank is None or rank > cfg["max_held_rank"]:
            px_row = scores[scores["symbol"] == sym]
            px = float(px_row["close"].iloc[0]) if not px_row.empty else None
            if px is None:
                actions.append(f"HOLD {sym}: no price in latest scan — manual review needed")
                continue
            _record(conn, book, day, sym, "SELL", h["shares"], px,
                    f"review: rank decayed to {rank or 'unranked'} "
                    f"(> {cfg['max_held_rank']})")
            still_held = set(holdings(conn, book)["symbol"])
            sector_counts: dict[str, int] = {}
            for hs in still_held:
                sec = (ranked.at[hs, "sector"] if hs in ranked.index else "Other") or "Other"
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
            candidates = ranked[~ranked.index.isin(still_held)].reset_index()
            repl = _select_diversified(candidates, 1, sector_counts)
            if not repl.empty:
                r = repl.iloc[0]
                budget = min(cash(conn, book),
                             cfg["start_cash"] / cfg["n_positions"] * 1.2)
                shares = int(budget // r["close"])
                if shares > 0:
                    _record(conn, book, day, r["symbol"], "BUY", shares, r["close"],
                            f"review: replacement, {cfg['label']} rank "
                            f"#{int(r['fc_rank'])}")
                    actions.append(f"SWAP {sym} -> {r['symbol']}")
    conn.commit()
    return actions


def snapshot(conn, book: str = "core",
             spy: float | None = None) -> dict:
    _, day, scores = latest_scan(conn)
    px = scores.set_index("symbol")["close"]
    held = holdings(conn, book)
    pos_val = 0.0
    for _, h in held.iterrows():
        p = px.get(h["symbol"])
        if p is None or pd.isna(p):
            last = conn.execute(
                "SELECT price FROM pf_txns WHERE book=? AND symbol=? "
                "ORDER BY txn_id DESC LIMIT 1", (book, h["symbol"])).fetchone()
            p = last[0] if last else 0.0
            log.warning("[%s] %s missing from latest scan — using last known price",
                        book, h["symbol"])
        pos_val += h["shares"] * float(p)
    c = cash(conn, book)
    if spy is None:
        spy = spy_close()
    conn.execute("INSERT OR REPLACE INTO pf_snapshots VALUES (?,?,?,?,?,?)",
                 (day, book, round(pos_val, 2), c, round(pos_val + c, 2), spy))
    conn.commit()
    return {"date": day, "book": book, "positions_value": round(pos_val, 2),
            "cash": c, "total": round(pos_val + c, 2), "spy_close": spy}


def position_table(conn, book: str = "core") -> pd.DataFrame:
    _, _, scores = latest_scan(conn)
    ranked = book_ranked(scores, book).set_index("symbol")
    px = scores.set_index("symbol")["close"]
    held = holdings(conn, book)
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
    if not rows:
        return pd.DataFrame(columns=["symbol", "shares", "cost_basis", "price",
                                     "value", "pnl", "pnl_pct", "rank_now"])
    return pd.DataFrame(rows).sort_values("value", ascending=False)
