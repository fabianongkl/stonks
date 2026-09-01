"""Reporting: the human-friendly HTML dashboard and the markdown journal.

The dashboard is a single self-contained HTML file (no external requests, no
build step) regenerated after every scan.  It shows:

  * today's top-ranked stocks with a plain-English explanation of WHY each
    one scored well,
  * the current factor weights and how the learning module has moved them,
  * the track record: how past scans' top picks actually performed versus
    the market — the honest scoreboard this whole project exists for.

The journal is an append-only markdown record, one file per scan day.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

from . import commentary, config, db, learning
from .factors import FACTOR_NAMES

# ---------------------------------------------------------------------------
# Plain-English reasons
# ---------------------------------------------------------------------------

_REASON_RULES = [
    # (column, high_is_good, percentile threshold, text)
    ("earnings_yield", True, 0.80, "cheap versus its earnings"),
    ("fcf_yield", True, 0.80, "generates a lot of free cash for its price"),
    ("book_to_market", True, 0.80, "priced low versus its book value"),
    ("gross_profitability", True, 0.80, "highly profitable on its assets"),
    ("roa", True, 0.80, "strong return on assets"),
    ("leverage", False, 0.20, "conservatively financed (low debt)"),
    ("accruals", False, 0.20, "earnings backed by real cash flow"),
    ("mom_12_1", True, 0.80, "in a strong 12-month uptrend"),
    ("volatility", False, 0.20, "trades steadily (low volatility)"),
    ("revenue_growth", True, 0.80, "growing revenue quickly"),
    ("issuance", False, 0.20, "buying back its own shares"),
    ("asset_growth", False, 0.20, "growing with discipline, not empire-building"),
    ("insider_net_mcap", True, 0.90, "insiders are buying with their own money"),
]


def _reasons(row: pd.Series, pct: pd.DataFrame) -> list[str]:
    out = []
    for col, high_good, thresh, text in _REASON_RULES:
        p = pct.at[row.name, col] if col in pct.columns else np.nan
        if pd.isna(p):
            continue
        if (high_good and p >= thresh) or (not high_good and p <= thresh):
            out.append(text)
    return out[:4]


def _flags(row: pd.Series, pct: pd.DataFrame) -> list[str]:
    """Honest warnings shown alongside the praise."""
    out = []
    for col, high_good, thresh, text in [
        ("volatility", False, 0.85, "very volatile"),
        ("leverage", False, 0.90, "heavily indebted"),
        ("accruals", False, 0.90, "earnings not backed by cash flow"),
        ("mom_12_1", True, 0.15, "in a long downtrend"),
        ("dollar_volume", True, 0.10, "thinly traded"),
        ("issuance", False, 0.90, "heavily diluting shareholders"),
        ("insider_net_mcap", True, 0.05, "insiders selling heavily"),
    ]:
        p = pct.at[row.name, col] if col in pct.columns else np.nan
        if pd.isna(p):
            continue
        if (high_good and p <= thresh) or (not high_good and p >= thresh):
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def _fmt_mcap(v) -> str:
    if not v or pd.isna(v):
        return "—"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:,.0f}"


def build_payload(conn: sqlite3.Connection, scan_id: int) -> dict:
    scan = pd.read_sql_query("SELECT * FROM scans WHERE scan_id=?", conn,
                             params=(scan_id,)).iloc[0]
    scores = db.get_scores(conn, scan_id)
    weights = json.loads(scan["weights_json"])

    pct = scores[[c for c, *_ in _REASON_RULES] + ["dollar_volume"]].rank(pct=True)

    def _pick(row, display_rank: int) -> dict:
        return {
            "rank": display_rank,
            "symbol": row["symbol"],
            "name": (row["name"] or "")[:60],
            "sector": row.get("sector") or "—",
            "close": round(float(row["close"]), 2),
            "mcap": _fmt_mcap(row["market_cap"]),
            "composite": round(float(row["composite"]), 2),
            "factors": {f: (None if pd.isna(row[f"{f}_score"])
                            else round(float(row[f"{f}_score"]), 2))
                        for f in FACTOR_NAMES},
            "reasons": _reasons(row, pct),
            "flags": _flags(row, pct),
        }

    full = commentary.full_coverage(scores)
    picks = [_pick(row, int(row["fc_rank"]))
             for _, row in full.head(100).iterrows()]

    # previous scan (different date) for movers/entrants commentary
    prev_row = conn.execute(
        "SELECT scan_id FROM scans WHERE scan_date < ? "
        "ORDER BY scan_date DESC, scan_id DESC LIMIT 1",
        (scan["scan_date"],)).fetchone()
    prev_full = None
    if prev_row:
        prev_scores = db.get_scores(conn, prev_row[0])
        if not prev_scores.empty and "sector" in prev_scores.columns:
            prev_full = commentary.full_coverage(prev_scores)

    overall_text = commentary.overall(full, prev_full, scan["scan_date"],
                                      int(scan["scored_size"]))
    sector_data = {}
    for sec_name, cnt in full["sector"].value_counts().items():
        if not sec_name or cnt < 5:
            continue
        sec_top = full[full["sector"] == sec_name].head(10)
        sector_data[sec_name] = {
            "commentary": commentary.sector(full, prev_full, sec_name),
            "picks": [_pick(row, int(row["fc_rank"]))
                      for _, row in sec_top.iterrows()],
        }

    # Track record
    tr = learning.track_record(conn)
    track = [
        {"date": r["scan_date"], "horizon": int(r["horizon_days"]),
         "top": round(float(r["top_decile_return"]) * 100, 2),
         "median": round(float(r["universe_median_return"]) * 100, 2),
         "spread": round(float(r["spread"]) * 100, 2),
         "p": (round(float(r["p_value"]), 3)
               if "p_value" in r and pd.notna(r["p_value"]) else None)}
        for _, r in tr.iterrows()
    ]

    ic = pd.read_sql_query(
        "SELECT factor, AVG(ic) AS mean_ic, COUNT(*) AS n FROM factor_ic "
        "WHERE horizon_days=? AND ic IS NOT NULL GROUP BY factor",
        conn, params=(config.PRIMARY_HORIZON,))
    ics = {r["factor"]: {"mean": round(float(r["mean_ic"]), 4), "n": int(r["n"])}
           for _, r in ic.iterrows()}

    wh = pd.read_sql_query(
        "SELECT effective_date, weights_json, reason FROM weights_history "
        "ORDER BY effective_date", conn)
    weight_history = [
        {"date": r["effective_date"], "weights": json.loads(r["weights_json"]),
         "reason": r["reason"]}
        for _, r in wh.iterrows()
    ]

    scan_dates = pd.read_sql_query(
        "SELECT scan_date, scored_size, full_coverage FROM scans ORDER BY scan_date",
        conn)

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "scan_date": scan["scan_date"],
        "universe_size": int(scan["universe_size"]),
        "scored_size": int(scan["scored_size"]),
        "full_coverage": int(scan["full_coverage"]),
        "weights": weights,
        "picks": picks,
        "overall_commentary": overall_text,
        "sectors": sector_data,
        "track": track,
        "factor_ics": ics,
        "weight_history": weight_history,
        "scan_history": scan_dates.to_dict("records"),
        "horizons": config.EVAL_HORIZONS_DAYS,
        "primary_horizon": config.PRIMARY_HORIZON,
    }


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def write_journal_entry(conn: sqlite3.Connection, scan_id: int, today: str) -> None:
    payload = build_payload(conn, scan_id)
    lines = [
        f"# Scan — {today}",
        "",
        f"- Universe: {payload['universe_size']} listed stocks; "
        f"{payload['scored_size']} scored; {payload['full_coverage']} with full factor coverage",
        f"- Factor weights: " + ", ".join(
            f"{f} {payload['weights'].get(f, 0):.2f}" for f in FACTOR_NAMES),
        "",
        "## Top 10",
        "",
        "| # | Ticker | Composite | Why |",
        "|---|--------|-----------|-----|",
    ]
    for p in payload["picks"][:10]:
        lines.append(f"| {p['rank']} | {p['symbol']} | {p['composite']:+.2f} | "
                     f"{'; '.join(p['reasons']) or '—'} |")
    if payload["track"]:
        lines += ["", "## Track record to date", "",
                  "| Scan | Horizon | Top decile | Market median | Spread |",
                  "|------|---------|-----------|---------------|--------|"]
        for t in payload["track"]:
            lines.append(f"| {t['date']} | {t['horizon']}d | {t['top']:+.1f}% | "
                         f"{t['median']:+.1f}% | {t['spread']:+.1f}% |")
    (config.JOURNAL_DIR / f"{today}.md").write_text("\n".join(lines) + "\n",
                                                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def generate_dashboard(conn: sqlite3.Connection, scan_id: int) -> str:
    payload = build_payload(conn, scan_id)
    html = _TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))
    out = config.DASHBOARD_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    # also keep a JSON snapshot per scan for programmatic users
    (config.DASHBOARD_DIR / "data").mkdir(exist_ok=True)
    (config.DASHBOARD_DIR / "data" / f"{payload['scan_date']}.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    return str(out)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Screener</title>
<style>
:root{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
  --border:rgba(11,11,11,.10);
  --pos:#2a78d6; --neg:#e34948; --neutral:#f0efec;
  --good:#006300; --warn:#b45309;
  --chip:#f0efec;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835;
    --border:rgba(255,255,255,.10);
    --pos:#3987e5; --neg:#e66767; --neutral:#383835;
    --good:#0ca30c; --warn:#fab219;
    --chip:#262624;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835;
  --border:rgba(255,255,255,.10);
  --pos:#3987e5; --neg:#e66767; --neutral:#383835;
  --good:#0ca30c; --warn:#fab219;
  --chip:#262624;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--page);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px 16px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:1.5rem;margin-bottom:2px}
h2{font-size:1.1rem;margin:32px 0 10px}
.sub{color:var(--ink2)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;min-width:150px;flex:1}
.tile .v{font-size:1.5rem;font-weight:650}
.tile .l{color:var(--ink2);font-size:.8rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th{color:var(--ink2);font-weight:600;text-align:left;padding:6px 10px;
  border-bottom:1px solid var(--axis);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
td.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
tr:hover td{background:color-mix(in srgb, var(--ink) 4%, transparent)}
.tick{font-weight:650}
.why{color:var(--ink2);font-size:.82rem;max-width:330px}
.flag{color:var(--warn);font-size:.78rem}
.fbars{display:flex;gap:3px;align-items:flex-end;height:26px}
.fbar{width:9px;border-radius:2px 2px 0 0;background:var(--pos);position:relative}
.fbar.neg{background:var(--neg);border-radius:0 0 2px 2px}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--ink2);
  font-size:.8rem;margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}
.wrow{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:.87rem}
.wname{width:110px;color:var(--ink2)}
.wbarbg{flex:1;background:var(--neutral);border-radius:4px;height:10px}
.wbar{height:10px;border-radius:4px;background:var(--pos)}
.wval{width:48px;text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);padding:24px;text-align:center}
input[type=search]{background:var(--surface);color:var(--ink);
  border:1px solid var(--axis);border-radius:8px;padding:7px 12px;
  font:inherit;width:230px;margin-bottom:10px}
select{background:var(--surface);color:var(--ink);border:1px solid var(--axis);
  border-radius:8px;padding:7px 10px;font:inherit}
.note{color:var(--muted);font-size:.8rem;margin-top:8px}
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
<div class="wrap">
  <h1>Open Screener</h1>
  <div class="sub" id="head-sub"></div>

  <div class="tiles" id="tiles"></div>

  <h2>Top-ranked stocks</h2>
  <div class="sub" style="font-size:.85rem;margin-bottom:8px">
    Ranked by composite factor score among stocks with complete data on all four
    factors. Every metric is ranked <i>within its sector</i>, so a stock earns
    its place by beating its own industry's peers, not by belonging to an
    industry that flatters the metric. Mini-bars show Value, Quality, Momentum
    and Low-Vol scores (up = above sector average, down = below).
  </div>
  <div class="card" style="margin-bottom:12px">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <label for="sector-sel" style="color:var(--ink2);font-size:.85rem">View:</label>
      <select id="sector-sel"></select>
    </div>
    <div id="commentary" style="margin-top:10px;font-size:.9rem"></div>
    <div class="note">Auto-generated from scan data by fixed rules — every
      sentence is derived from the numbers, no AI involved in the daily run.</div>
  </div>
  <div class="card">
    <input type="search" id="q" placeholder="Filter by ticker or name…">
    <div class="legend">
      <span><span class="sw" style="background:var(--pos)"></span>above average</span>
      <span><span class="sw" style="background:var(--neg)"></span>below average</span>
      <span>V = Value · Q = Quality · M = Momentum · L = Low-Vol</span>
    </div>
    <table id="picks">
      <thead><tr>
        <th>#</th><th>Ticker</th><th>Company</th><th>Sector</th>
        <th class="num">Price</th>
        <th class="num">Mkt cap</th><th class="num">Score</th>
        <th>V Q M L</th><th>Why it ranks here</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <h2>Track record — do the picks actually work?</h2>
  <div class="sub" style="font-size:.85rem;margin-bottom:8px">
    For every past scan that has matured, the average forward return of the top
    10% of ranked stocks versus the median stock in the whole scan. A positive
    spread means the screener's ranking carried real information.
  </div>
  <div class="card" id="track-card"></div>

  <h2>Factor weights &amp; learning</h2>
  <div class="card">
    <div id="weights"></div>
    <div id="ics" class="note"></div>
    <div id="whistory" class="note"></div>
  </div>

  <div class="disclaimer">
    <b>Not investment advice.</b> This is an open research tool that ranks
    stocks by published academic factors and then honestly measures whether
    those rankings predicted anything. Factor data comes from SEC filings and
    free end-of-day prices, both of which can be stale or wrong for individual
    stocks. Verify everything before risking money, and read
    <i>docs/METHODOLOGY.md</i> for what these scores do and do not mean.
  </div>
  <div class="note" id="footer"></div>
</div>
<div class="tooltip" id="tip"></div>

<script>
const D = __PAYLOAD__;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

$('#head-sub').innerHTML =
  `Scan of ${D.scan_date} · a transparent, self-improving factor screener · generated ${D.generated}` +
  ` · <a href="portfolio.html">Claude's Picks portfolio →</a>`;

/* --- stat tiles --- */
const matured = new Set(D.track.map(t => t.date)).size;
$('#tiles').innerHTML = [
  [D.universe_size.toLocaleString(), 'US stocks in universe'],
  [D.scored_size.toLocaleString(), 'scored this scan'],
  [D.full_coverage.toLocaleString(), 'with full factor data'],
  [D.scan_history.length, 'scans recorded'],
  [matured, 'scans matured &amp; evaluated'],
].map(([v, l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

/* --- picks table --- */
function fbars(f) {
  const H = 12; // px per z unit, clamp ±1.3 units of bar
  return '<div class="fbars">' + ['value','quality','momentum','low_vol'].map(k => {
    const z = f[k];
    if (z === null || z === undefined)
      return `<span class="fbar" style="height:2px;background:var(--axis)" title="no data"></span>`;
    const h = Math.min(Math.abs(z), 1.3) * H + 2;
    const cls = z < 0 ? 'fbar neg' : 'fbar';
    const off = z < 0 ? `margin-top:${13}px` : `margin-top:${13 - h}px`;
    return `<span class="${cls}" style="height:${h}px;align-self:${z<0?'flex-start':'flex-end'}" title="${k}: ${z>0?'+':''}${z}"></span>`;
  }).join('') + '</div>';
}
/* --- sector selector & commentary --- */
const sel = $('#sector-sel');
const sectorNames = Object.keys(D.sectors || {});
sel.innerHTML = '<option value="ALL">All sectors (top 100)</option>' +
  sectorNames.map(s => `<option value="${esc(s)}">${esc(s)} (top 10)</option>`).join('');
function currentPicks() {
  const v = sel.value;
  return v === 'ALL' ? D.picks : (D.sectors[v]?.picks || []);
}
function renderCommentary() {
  const v = sel.value;
  $('#commentary').textContent = v === 'ALL'
    ? (D.overall_commentary || '')
    : (D.sectors[v]?.commentary || '');
}
sel.addEventListener('change', () => { renderCommentary(); renderPicks($('#q').value); });

function renderPicks(filter) {
  const q = (filter || '').trim().toUpperCase();
  const rows = currentPicks().filter(p => !q ||
    p.symbol.includes(q) || p.name.toUpperCase().includes(q));
  $('#picks tbody').innerHTML = rows.map(p => `
    <tr>
      <td class="num">${p.rank}</td>
      <td class="tick">${esc(p.symbol)}</td>
      <td>${esc(p.name)}</td>
      <td class="why">${esc(p.sector || '—')}</td>
      <td class="num">$${p.close.toLocaleString()}</td>
      <td class="num">${p.mcap}</td>
      <td class="num">${p.composite > 0 ? '+' : ''}${p.composite.toFixed(2)}</td>
      <td>${fbars(p.factors)}</td>
      <td class="why">${esc(p.reasons.join('; ') || '—')}
        ${p.flags.length ? `<div class="flag">⚠ ${esc(p.flags.join('; '))}</div>` : ''}</td>
    </tr>`).join('') ||
    '<tr><td colspan="9" class="empty">No matches.</td></tr>';
}
renderCommentary();
renderPicks('');
$('#q').addEventListener('input', e => renderPicks(e.target.value));

/* --- track record chart --- */
(function () {
  const card = $('#track-card');
  const rows = D.track.filter(t => t.horizon === D.primary_horizon);
  const all = D.track;
  if (!all.length) {
    card.innerHTML = `<div class="empty">No scans have matured yet. The first
      evaluation appears ${D.horizons[0]} trading days (≈1 month) after the
      first scan — the scoreboard fills in from there.</div>`;
    return;
  }
  const data = rows.length ? rows : all;
  const W = Math.max(560, data.length * 46 + 90), H = 220,
        padL = 46, padR = 10, padT = 14, padB = 34;
  const vals = data.map(d => d.spread);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = (hi - lo) || 1; lo -= span * .1; hi += span * .1;
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const bw = Math.min(26, (W - padL - padR) / data.length - 6);
  let g = '';
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + (hi - lo) * i / ticks, yy = y(v);
    g += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`
       + `<text x="${padL - 6}" y="${yy + 4}" text-anchor="end">${v.toFixed(1)}%</text>`;
  }
  g += `<line x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}" stroke="var(--axis)"/>`;
  data.forEach((d, i) => {
    const x = padL + 8 + i * ((W - padL - padR - 8) / data.length);
    const y0 = y(Math.max(0, d.spread)), h = Math.abs(y(d.spread) - y(0)) || 1;
    g += `<rect class="bar" data-i="${i}" x="${x}" y="${y0}" width="${bw}" height="${h}"
           rx="3" fill="${d.spread >= 0 ? 'var(--pos)' : 'var(--neg)'}"/>`;
    if (i % Math.ceil(data.length / 12) === 0)
      g += `<text x="${x + bw / 2}" y="${H - padB + 16}" text-anchor="middle">${d.date.slice(5)}</text>`;
  });
  card.innerHTML =
    `<div class="legend">
       <span><span class="sw" style="background:var(--pos)"></span>picks beat market</span>
       <span><span class="sw" style="background:var(--neg)"></span>picks lagged market</span>
       <span>horizon: ${rows.length ? D.primary_horizon : 'all'} trading days</span>
     </div>
     <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
          aria-label="Top-decile return minus market median per scan">${g}</svg>`;
  const tip = $('#tip');
  card.querySelectorAll('.bar').forEach(b => {
    b.addEventListener('mousemove', e => {
      const d = data[+b.dataset.i];
      tip.style.display = 'block';
      tip.style.left = (e.clientX + 14) + 'px';
      tip.style.top = (e.clientY - 10) + 'px';
      tip.innerHTML = `<b>${d.date}</b> · ${d.horizon}d<br>
        top decile: ${d.top > 0 ? '+' : ''}${d.top}%<br>
        market median: ${d.median > 0 ? '+' : ''}${d.median}%<br>
        <b>spread: ${d.spread > 0 ? '+' : ''}${d.spread}%</b>` +
        (d.p !== null && d.p !== undefined
          ? `<br>p vs random picks: ${d.p} ${d.p < 0.05 ? '(beat luck)' : ''}`
          : '');
    });
    b.addEventListener('mouseleave', () => tip.style.display = 'none');
  });
})();

/* --- weights --- */
const WNAMES = {value:'Value', quality:'Quality', momentum:'Momentum', low_vol:'Low volatility'};
$('#weights').innerHTML = Object.keys(WNAMES).map(k => {
  const w = D.weights[k] || 0;
  return `<div class="wrow"><div class="wname">${WNAMES[k]}</div>
    <div class="wbarbg"><div class="wbar" style="width:${(w * 100).toFixed(0)}%"></div></div>
    <div class="wval">${(w * 100).toFixed(0)}%</div></div>`;
}).join('');
const icKeys = Object.keys(D.factor_ics);
$('#ics').textContent = icKeys.length
  ? 'Measured predictive power so far (mean rank-correlation with ' +
    D.primary_horizon + 'd forward returns): ' +
    icKeys.map(k => `${WNAMES[k] || k} ${D.factor_ics[k].mean >= 0 ? '+' : ''}${D.factor_ics[k].mean}`).join(' · ')
  : 'No factor predictive-power measurements yet — they accumulate as scans mature.';
$('#whistory').textContent = D.weight_history.length
  ? `Weights last adapted ${D.weight_history[D.weight_history.length - 1].date}: ` +
    D.weight_history[D.weight_history.length - 1].reason
  : 'Weights are still at their research-based starting values; the learning ' +
    'module adapts them once enough scans have matured.';

$('#footer').textContent =
  'Open Screener — free data (SEC EDGAR, Yahoo Finance), documented methodology, permanent records. See docs/ for details.';
</script>
</body>
</html>
"""
