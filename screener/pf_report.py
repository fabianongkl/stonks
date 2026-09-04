"""Dashboard page for the portfolio experiment (dashboard/portfolio.html)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd

from . import config, nav, portfolio


def build_payload(conn: sqlite3.Connection, book: str = "core") -> dict:
    cfg = portfolio.BOOKS[book]
    start_cash = cfg["start_cash"]
    pos = portfolio.position_table(conn, book)
    snaps = pd.read_sql_query(
        "SELECT * FROM pf_snapshots WHERE book=? ORDER BY date",
        conn, params=(book,))
    tx = pd.read_sql_query(
        "SELECT * FROM pf_txns WHERE book=? ORDER BY txn_id",
        conn, params=(book,))
    c = portfolio.cash(conn, book)
    total = round(float(pos["value"].sum()) + c, 2) if not pos.empty else c

    curve = []
    spy0 = None
    for _, s in snaps.iterrows():
        if spy0 is None and s["spy_close"]:
            spy0 = s["spy_close"]
        curve.append({
            "date": s["date"],
            "total": s["total"],
            "pf_idx": round(s["total"] / start_cash * 100, 2),
            "spy_idx": (round(s["spy_close"] / spy0 * 100, 2)
                        if spy0 and s["spy_close"] else None),
        })

    if cfg.get("style") == "ritual" and cfg.get("worst"):
        desc = ("the Loser Reversal ritual: each January, sell everything and "
                "buy the prior calendar year's three WORST S&P 500 performers "
                "(the Top-3 Ritual's mirror image)")
        rule_note = ("Rank now = trailing-12-month return rank from the "
                     "bottom among S&P 500 members (informational). One "
                     "trade a year — the January rotation — no reviews, "
                     "no stops.")
    elif cfg.get("style") == "ritual":
        desc = ("the Top-3 ritual: each January, sell everything and buy the "
                "prior calendar year's three biggest S&P 500 gainers")
        rule_note = ("Rank now = trailing-12-month return rank among current "
                     "S&P 500 members (informational). This book trades "
                     "exactly once a year — the January rotation — no "
                     "monthly reviews, no stops.")
    elif cfg.get("style") == "monkey":
        desc = ("the control group: 10 stocks drawn at random (seeded, "
                "reproducible) from the same pool the factor books pick "
                "from, redrawn each January")
        rule_note = ("Rank now = the screener's composite rank for each "
                     "random pick (informational — what the model thinks of "
                     "the monkey's luck). Any book that can't beat this one "
                     "has no business claiming skill.")
    elif cfg.get("style") == "insider":
        desc = ("follow the insiders: the 8 stocks with the largest net "
                "insider open-market buying relative to market cap "
                "(SEC Form 4 data), rotated each calendar quarter")
        rule_note = ("Rank now = net-insider-buying rank (market cap ≥ "
                     "$250M). Rotates on the first scan of each quarter; "
                     "the underlying SEC data set publishes with a lag of "
                     "one to two quarters — a documented limitation.")
    elif cfg["weights"] is None:
        desc = "the screener's own composite, patient rules"
        rule_note = (f"Rank now = current composite rank among full-coverage "
                     f"stocks. Holdings are reviewed monthly; a rank decayed "
                     f"past #{cfg['max_held_rank']} triggers replacement.")
    else:
        desc = ("momentum-dominant weighting ("
                + ", ".join(f"{k} {v:.0%}" for k, v in cfg["weights"].items())
                + "), concentrated and fast-rotating")
        rule_note = (f"Rank now = this book's own momentum-tilted rank. "
                     f"Holdings are reviewed monthly; a rank decayed past "
                     f"#{cfg['max_held_rank']} triggers replacement.")

    fees = round(float(tx["fee"].sum()), 2) if not tx.empty else 0.0
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "label": cfg["label"],
        "book": book,
        "desc": desc,
        "rule_note": rule_note,
        "others": [{"label": portfolio.BOOKS[b]["label"],
                    "page": portfolio.BOOKS[b]["page"]}
                   for b in portfolio.BOOKS if b != book],
        "start_cash": start_cash,
        "total": total,
        "cash": c,
        "pnl": round(total - start_cash, 2),
        "pnl_pct": round((total / start_cash - 1) * 100, 2),
        "fees_paid": fees,
        "positions": pos.to_dict("records") if not pos.empty else [],
        "curve": curve,
        "txns": [
            {"date": t["date"], "side": t["side"], "symbol": t["symbol"],
             "shares": t["shares"], "price": t["price"], "fee": t["fee"],
             "note": t["note"]}
            for _, t in tx.iterrows()
        ],
        "max_held_rank": cfg["max_held_rank"],
    }


def generate(conn: sqlite3.Connection, book: str = "core") -> str:
    payload = build_payload(conn, book)
    html = (_TEMPLATE
            .replace("__TITLE__", payload["label"])
            .replace("__NAV__", nav.nav_html(portfolio.BOOKS[book]["page"]))
            .replace("__PAYLOAD__", json.dumps(payload)))
    out = config.DASHBOARD_DIR / portfolio.BOOKS[book]["page"]
    out.write_text(html, encoding="utf-8")
    return str(out)


def generate_all(conn: sqlite3.Connection) -> list[str]:
    return [generate(conn, b) for b in portfolio.BOOKS
            if not portfolio.holdings(conn, b).empty
            or b == "core"]


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
  --border:rgba(11,11,11,.10);
  --pos:#2a78d6; --neg:#e34948; --neutral:#f0efec;
  --good:#006300; --warn:#b45309; --spy:#eb6834;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835;
    --border:rgba(255,255,255,.10);
    --pos:#3987e5; --neg:#e66767; --neutral:#383835;
    --good:#0ca30c; --warn:#fab219; --spy:#d95926;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835;
  --border:rgba(255,255,255,.10);
  --pos:#3987e5; --neg:#e66767; --neutral:#383835;
  --good:#0ca30c; --warn:#fab219; --spy:#d95926;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--page);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px 16px}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:1.5rem;margin-bottom:2px}
h2{font-size:1.05rem;margin:30px 0 10px}
.sub{color:var(--ink2)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;min-width:140px;flex:1}
.tile .v{font-size:1.4rem;font-weight:650}
.tile .l{color:var(--ink2);font-size:.8rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th{color:var(--ink2);font-weight:600;text-align:left;padding:6px 10px;
  border-bottom:1px solid var(--axis);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--grid)}
td.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.up{color:var(--good)} .down{color:var(--neg)}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--ink2);
  font-size:.8rem;margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}
.note{color:var(--muted);font-size:.8rem;margin-top:8px}
.empty{color:var(--muted);padding:24px;text-align:center}
.disclaimer{border:1px solid var(--border);border-left:3px solid var(--warn);
  border-radius:8px;padding:10px 14px;margin:26px 0;color:var(--ink2);font-size:.85rem}
svg text{fill:var(--ink2);font:11px system-ui,sans-serif}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);
  border:1px solid var(--border);border-radius:8px;padding:8px 10px;
  font-size:.8rem;box-shadow:0 4px 14px rgba(0,0,0,.18);display:none;z-index:9}
a{color:var(--pos)}
</style>
</head>
<body>
__NAV__
<div class="wrap">
  <h1 id="pf-title"></h1>
  <div class="sub" id="pf-sub"></div>

  <div class="tiles" id="tiles"></div>

  <h2>Portfolio vs SPY (indexed to 100 at inception)</h2>
  <div class="card" id="curve-card"></div>

  <h2>Positions</h2>
  <div class="card">
    <table id="pos">
      <thead><tr>
        <th>Ticker</th><th class="num">Shares</th><th class="num">Cost</th>
        <th class="num">Price</th><th class="num">Value</th>
        <th class="num">P&amp;L</th><th class="num">P&amp;L %</th>
        <th class="num">Rank now</th>
      </tr></thead><tbody></tbody>
    </table>
    <div class="note" id="rule-note"></div>
  </div>

  <h2>Transaction log</h2>
  <div class="card">
    <table id="tx">
      <thead><tr><th>Date</th><th>Side</th><th>Ticker</th>
        <th class="num">Shares</th><th class="num">Price</th>
        <th class="num">Fee</th><th>Note</th></tr></thead><tbody></tbody>
    </table>
  </div>

  <div class="disclaimer"><b>Hypothetical portfolio — no real money.</b>
    This is a live accountability experiment for the screener's rankings,
    including IBKR-style commissions. It is not investment advice, and its
    concentration is a research choice, not a recommendation.</div>
  <div class="note" id="footer"></div>
</div>
<div class="tooltip" id="tip"></div>

<script>
const D = __PAYLOAD__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money = v => '$' + Number(v).toLocaleString(undefined,
  {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
const sign = v => (v > 0 ? '+' : '') + v;

$('#pf-title').textContent = D.label;
$('#pf-sub').textContent = `A hypothetical ${money(D.start_cash)} paper book — ${D.desc} — tracked daily, fees included, versus buy-and-hold SPY.`;
$('#rule-note').textContent = D.rule_note;
$('#tiles').innerHTML = [
  [money(D.total), 'total value'],
  [`<span class="${cls(D.pnl)}">${sign(D.pnl_pct)}%</span>`, `P&L (${money(D.pnl)})`],
  [money(D.cash), 'cash'],
  [money(D.fees_paid), 'fees paid'],
  [D.positions.length, 'positions'],
].map(([v,l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

$('#pos tbody').innerHTML = D.positions.map(p => `
  <tr><td><b>${esc(p.symbol)}</b></td>
    <td class="num">${p.shares}</td>
    <td class="num">${money(p.cost_basis)}</td>
    <td class="num">${money(p.price)}</td>
    <td class="num">${money(p.value)}</td>
    <td class="num ${cls(p.pnl)}">${money(p.pnl)}</td>
    <td class="num ${cls(p.pnl)}">${sign(p.pnl_pct)}%</td>
    <td class="num">${p.rank_now ?? '—'}</td></tr>`).join('')
  || '<tr><td colspan="8" class="empty">No positions.</td></tr>';

$('#tx tbody').innerHTML = D.txns.map(t => `
  <tr><td>${t.date}</td><td>${t.side}</td><td><b>${esc(t.symbol)}</b></td>
    <td class="num">${t.shares}</td><td class="num">${money(t.price)}</td>
    <td class="num">${money(t.fee)}</td><td class="note">${esc(t.note)}</td></tr>`).join('');

/* equity curve */
(function () {
  const card = $('#curve-card');
  const data = D.curve.filter(c => c.pf_idx !== null);
  if (data.length < 2) {
    card.innerHTML = `<div class="empty">The curve appears from the second
      daily snapshot onward — run the tracker again tomorrow.</div>`;
    return;
  }
  const W = 940, H = 240, padL = 44, padR = 14, padT = 12, padB = 28;
  const all = data.flatMap(d => [d.pf_idx, d.spy_idx].filter(v => v !== null));
  let lo = Math.min(...all), hi = Math.max(...all);
  const span = (hi - lo) || 1; lo -= span * .1; hi += span * .1;
  const x = i => padL + i / (data.length - 1) * (W - padL - padR);
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, yy = y(v);
    g += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`
       + `<text x="${padL - 6}" y="${yy + 4}" text-anchor="end">${v.toFixed(0)}</text>`;
  }
  const path = (key, color) => {
    const pts = data.map((d, i) => d[key] === null ? null : `${x(i)},${y(d[key])}`)
      .filter(Boolean).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/>`;
  };
  g += path('spy_idx', 'var(--spy)') + path('pf_idx', 'var(--pos)');
  data.forEach((d, i) => {
    if (i % Math.ceil(data.length / 10) === 0)
      g += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${d.date.slice(5)}</text>`;
  });
  card.innerHTML = `
    <div class="legend">
      <span><span class="sw" style="background:var(--pos)"></span>Claude's Picks</span>
      <span><span class="sw" style="background:var(--spy)"></span>SPY</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
         aria-label="Portfolio versus SPY, indexed to 100">${g}</svg>`;
})();

$('#footer').textContent = `Generated ${D.generated} · started with ${money(D.start_cash)} · commissions use the IBKR Fixed model ($0.005/share, min $1, max 1% of trade value).`;
</script>
</body>
</html>
"""
