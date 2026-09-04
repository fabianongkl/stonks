"""S&P 500 section: index-only view of the daily scan, plus the
"winners persistence" study — do the previous 12 months' best performers
keep outperforming over the following 12 months?

Additive module: it changes nothing in the core pipeline; it reads the
existing scan from the database and computes its own price study.

Study construction (documented on the page):
  * At each month-end, rank current S&P members by trailing-12-month return.
  * "Winners" = top 50; "losers" = bottom 50.
  * Measure everyone's return over the NEXT 12 months; compare winners and
    losers to the median member and to SPY.
  * Rolling month-ends give statistical mass (overlapping!); calendar-year
    anchors (each December) give the clean, readable year-by-year answer.
Biases stated in the page: today's membership (survivorship), overlapping
rolling windows, and a short sample (this window, not a universal law).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from . import commentary, config, db
from .data import sp500

log = logging.getLogger(__name__)

N_GROUP = 50   # winners / losers group size


def _month_end_matrix(hist: pd.DataFrame) -> pd.DataFrame:
    C = hist.pivot_table(index="date", columns="symbol", values="close")
    C.index = pd.to_datetime(C.index)
    C = C.sort_index().ffill(limit=10)
    return C.groupby([C.index.year, C.index.month]).tail(1)


def winners_study(hist: pd.DataFrame) -> dict:
    Cm = _month_end_matrix(hist)
    spy = Cm["SPY"]
    members = Cm.drop(columns=["SPY"])
    rows = []
    for t in range(12, len(Cm) - 12):
        form = members.iloc[t] / members.iloc[t - 12] - 1
        fwd = members.iloc[t + 12] / members.iloc[t] - 1
        ok = form.notna() & fwd.notna()
        if ok.sum() < 300:
            continue
        f, w = form[ok], fwd[ok]
        winners = f.nlargest(N_GROUP).index
        losers = f.nsmallest(N_GROUP).index
        rows.append({
            "anchor": Cm.index[t].strftime("%Y-%m"),
            "is_december": Cm.index[t].month == 12,
            "winners_fwd": float(w[winners].mean()),
            "losers_fwd": float(w[losers].mean()),
            "median_fwd": float(w.median()),
            "spy_fwd": float(spy.iloc[t + 12] / spy.iloc[t] - 1),
            "n": int(ok.sum()),
        })
    df = pd.DataFrame(rows)

    # partial current period: latest 12m winners, forward-to-date
    partial = None
    t = len(Cm) - 1
    dec_idx = [i for i, d in enumerate(Cm.index) if d.month == 12]
    if dec_idx and dec_idx[-1] >= 12 and t - dec_idx[-1] >= 2:
        a = dec_idx[-1]
        form = members.iloc[a] / members.iloc[a - 12] - 1
        fwd = members.iloc[t] / members.iloc[a] - 1
        ok = form.notna() & fwd.notna()
        f, w = form[ok], fwd[ok]
        winners = f.nlargest(N_GROUP).index
        partial = {
            "anchor": Cm.index[a].strftime("%Y-%m"),
            "through": Cm.index[t].strftime("%Y-%m"),
            "winners_fwd": float(w[winners].mean()),
            "median_fwd": float(w.median()),
            "spy_fwd": float(spy.iloc[t] / spy.iloc[a] - 1),
        }

    spread = df["winners_fwd"] - df["median_fwd"]
    return {
        "rolling_n": len(df),
        "mean_winners": float(df["winners_fwd"].mean()),
        "mean_losers": float(df["losers_fwd"].mean()),
        "mean_median": float(df["median_fwd"].mean()),
        "mean_spy": float(df["spy_fwd"].mean()),
        "mean_spread": float(spread.mean()),
        "hit_rate": float((spread > 0).mean()),
        "calendar": df[df["is_december"]].drop(columns=["is_december"])
                      .to_dict("records"),
        "partial": partial,
    }


def top3_sim(members: pd.DataFrame, start_year: int = 2011,
             start_cash: float = 10_000.0) -> dict:
    """The user's ritual: each New Year, buy last calendar year's top-3
    S&P performers equal-weight; sell at year end; repeat.  Versus SPY.

    Eligibility per formation year: current members whose index 'date added'
    precedes the formation year's end AND who have prices spanning it —
    removed companies remain invisible (survivorship, stated on the page).
    Fees: IBKR Fixed model per trade (6 trades/year).
    """
    from .portfolio import ibkr_fee

    hist = sp500.fetch_history(members["symbol"].tolist(),
                               start=f"{start_year - 1}-06-01", tag="hist15")
    C = hist.pivot_table(index="date", columns="symbol", values="close")
    C.index = pd.to_datetime(C.index)
    C = C.sort_index().ffill(limit=10)
    year_end = C.groupby(C.index.year).tail(1)
    years = [int(d.year) for d in year_end.index]
    added = pd.to_datetime(
        members.set_index("symbol")["date_added"], errors="coerce")

    spy_ye = year_end["SPY"]
    val, spy_val = start_cash, start_cash
    rows, curve = [], []
    for i, fy in enumerate(years[:-1]):
        hold_year = fy + 1
        if fy < start_year - 1:
            continue
        prev_idx = i - 1
        if prev_idx < 0:
            continue
        form = year_end.iloc[i] / year_end.iloc[prev_idx] - 1
        eligible = [s for s in form.dropna().index if s != "SPY"
                    and (pd.isna(added.get(s)) or added[s].year <= fy)]
        if len(eligible) < 100:
            continue
        top3 = form[eligible].nlargest(3)
        nxt = year_end.iloc[i + 1] / year_end.iloc[i] - 1
        pick_rets = [float(nxt.get(s)) if pd.notna(nxt.get(s)) else 0.0
                     for s in top3.index]
        # fees: sell 3 old + buy 3 new positions at ~val/3 each
        fees = 0.0
        for s in top3.index:
            px = float(year_end.iloc[i].get(s, 100.0))
            sh = (val / 3) / px
            fees += 2 * ibkr_fee(sh, px)
        strat_ret = float(np.mean(pick_rets))
        val = (val - fees) * (1 + strat_ret)
        spy_ret = float(spy_ye.iloc[i + 1] / spy_ye.iloc[i] - 1)
        spy_val *= 1 + spy_ret
        rows.append({
            "hold_year": hold_year,
            "picks": [{"symbol": s,
                       "formation": round(float(top3[s]) * 100, 1),
                       "next_ret": round(float(nxt.get(s, np.nan)) * 100, 1)
                       if pd.notna(nxt.get(s)) else None}
                      for s in top3.index],
            "strat_ret": round(strat_ret * 100, 1),
            "spy_ret": round(spy_ret * 100, 1),
            "value": round(val, 0),
            "spy_value": round(spy_val, 0),
            "n_eligible": len(eligible),
        })
        # monthly curve within the held year
        mask = (C.index.year == hold_year)
        month_last = C[mask].groupby(C[mask].index.month).tail(1)
        base = year_end.iloc[i]
        v0 = rows[-1]["value"] / (1 + strat_ret)
        s0 = spy_val / (1 + spy_ret)
        for d in month_last.index:
            pr = np.mean([float(month_last.loc[d].get(s, np.nan) / base.get(s) - 1)
                          if pd.notna(month_last.loc[d].get(s)) else 0.0
                          for s in top3.index])
            curve.append({
                "date": d.strftime("%Y-%m"),
                "strat": round(v0 * (1 + pr), 0),
                "spy": round(s0 * float(month_last.loc[d]["SPY"] / base["SPY"]), 0),
            })

    # a final row covering the still-running year is partial — label it and
    # compound CAGR over fractional years, not a phantom full year
    last_month = int(C.index[-1].month)
    if rows and years[-1] == C.index[-1].year and last_month < 12:
        rows[-1]["partial"] = True
        rows[-1]["through"] = C.index[-1].strftime("%Y-%m")
    n_years = len(rows)
    years_frac = (n_years - 1 + last_month / 12.0
                  if rows and rows[-1].get("partial") else float(n_years))
    cagr = (val / start_cash) ** (1 / years_frac) - 1 if n_years else float("nan")
    spy_cagr = (spy_val / start_cash) ** (1 / years_frac) - 1 if n_years else float("nan")
    cs = pd.Series([c["strat"] for c in curve], dtype=float)
    maxdd = float((cs / cs.cummax() - 1).min()) if len(cs) else float("nan")
    return {
        "start_cash": start_cash, "years": n_years,
        "final": round(val, 0), "spy_final": round(spy_val, 0),
        "cagr": round(cagr * 100, 1), "spy_cagr": round(spy_cagr * 100, 1),
        "maxdd": round(maxdd * 100, 1),
        "beat_years": sum(1 for r in rows if r["strat_ret"] > r["spy_ret"]),
        "rows": rows, "curve": curve,
    }


def build_payload(conn) -> dict:
    members = sp500.fetch_members()
    hist = sp500.fetch_history(members["symbol"].tolist())
    study = winners_study(hist)

    # index-only view of the EXISTING scan (no rescoring)
    sid = conn.execute("SELECT MAX(scan_id) FROM scans").fetchone()[0]
    scan_date = conn.execute("SELECT scan_date FROM scans WHERE scan_id=?",
                             (sid,)).fetchone()[0]
    scores = db.get_scores(conn, sid)
    full = commentary.full_coverage(scores)
    mem = set(members["symbol"])
    sp = full[full["symbol"].isin(mem)].reset_index(drop=True)
    sp["sp_rank"] = sp.index + 1
    gics = dict(zip(members["symbol"], members["gics_sector"]))

    def row(r):
        return {"sp_rank": int(r["sp_rank"]), "rank_all": int(r["fc_rank"]),
                "symbol": r["symbol"], "name": (r["name"] or "")[:48],
                "gics": gics.get(r["symbol"], "—"),
                "composite": round(float(r["composite"]), 2),
                "mom_12_1": (None if pd.isna(r["mom_12_1"])
                             else round(float(r["mom_12_1"]) * 100, 1))}

    top30 = [row(r) for _, r in sp.head(30).iterrows()]

    # current trailing-12m winners and how the screener rates them today
    Cm = _month_end_matrix(hist).drop(columns=["SPY"])
    form = (Cm.iloc[-1] / Cm.iloc[-13] - 1).dropna()
    cur_winners = []
    ranks = sp.set_index("symbol")
    for sym in form.nlargest(20).index:
        cur_winners.append({
            "symbol": sym,
            "trailing_12m": round(float(form[sym]) * 100, 1),
            "composite": (round(float(ranks.at[sym, "composite"]), 2)
                          if sym in ranks.index else None),
            "sp_rank": (int(ranks.at[sym, "sp_rank"])
                        if sym in ranks.index else None),
        })

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "scan_date": scan_date,
        "n_members": len(members),
        "n_scored": len(sp),
        "study": study,
        "top3": top3_sim(members),
        "top30": top30,
        "cur_winners": cur_winners,
        "n_group": N_GROUP,
    }


def generate(conn) -> str:
    payload = build_payload(conn)
    html = _TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))
    out = config.DASHBOARD_DIR / "sp500.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S&amp;P 500 Lens</title>
<style>
:root{color-scheme:light;
  --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
  --pos:#2a78d6;--neg:#e34948;--spy:#eb6834;--med:#898781;--warn:#b45309}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --pos:#3987e5;--neg:#e66767;--spy:#d95926;--med:#898781;--warn:#fab219}}
:root[data-theme="dark"]{color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
  --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --pos:#3987e5;--neg:#e66767;--spy:#d95926;--med:#898781;--warn:#fab219}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--page);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px 16px}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:1.5rem}h2{font-size:1.05rem;margin:28px 0 10px}
.sub{color:var(--ink2)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;min-width:150px;flex:1}
.tile .v{font-size:1.35rem;font-weight:650}.tile .l{color:var(--ink2);font-size:.8rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px;overflow-x:auto;margin-bottom:14px}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th{color:var(--ink2);font-weight:600;text-align:left;padding:6px 10px;
  border-bottom:1px solid var(--axis);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid var(--grid)}
td.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.up{color:#0ca30c}.down{color:var(--neg)}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--ink2);font-size:.8rem;margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}
svg text{fill:var(--ink2);font:11px system-ui,sans-serif}
.disclaimer{border:1px solid var(--border);border-left:3px solid var(--warn);
  border-radius:8px;padding:10px 14px;margin:20px 0;color:var(--ink2);font-size:.85rem}
a{color:var(--pos)}
.answer{font-size:.95rem;background:var(--surface);border:1px solid var(--border);
  border-radius:10px;padding:14px 16px;margin:14px 0}
</style>
</head>
<body>
<div class="wrap">
  <h1>S&amp;P 500 Lens</h1>
  <div class="sub" id="sub"><a href="index.html">← screener dashboard</a></div>

  <div class="disclaimer"><b>Read first.</b> This section uses TODAY'S index
  membership — companies dropped from the index in past years are invisible,
  which flatters historical "winners" somewhat. Rolling windows overlap, and
  ~5 years is a short sample: these numbers describe <i>this window</i>, not a
  law of markets. The scan scores shown are the main screener's, unchanged —
  this page is a lens, not a different model.</div>

  <div class="answer" id="answer"></div>
  <div class="tiles" id="tiles"></div>

  <h2>Year by year: last year's top-50 vs the field, following 12 months</h2>
  <div class="card" id="calchart"></div>
  <div class="card"><table id="cal">
    <thead><tr><th>Formation year end</th><th class="num">Winners next-12m</th>
    <th class="num">Losers next-12m</th><th class="num">Median member</th>
    <th class="num">SPY</th><th class="num">Winners − median</th></tr></thead>
    <tbody></tbody></table></div>

  <h2>The Top-3 ritual: buy each year's 3 biggest winners, hold a year, repeat</h2>
  <div class="answer" id="t3answer"></div>
  <div class="card" id="t3chart"></div>
  <div class="card"><table id="t3">
    <thead><tr><th>Held year</th><th>The 3 picks (prior-year gain → held-year return)</th>
    <th class="num">Strategy year</th><th class="num">SPY year</th>
    <th class="num">Strategy $</th><th class="num">SPY $</th></tr></thead>
    <tbody></tbody></table>
    <div class="sub" style="font-size:.8rem;margin-top:8px">Eligibility uses each
    company's index join date, so members added later are excluded from earlier
    years — but companies REMOVED from the index are invisible (survivorship),
    and picks that later delisted are marked at last price. IBKR-model fees
    included (6 trades/yr). Equal-weight thirds, fractional shares.</div></div>

  <h2>Today's trailing-12-month winners — and what the screener thinks of them</h2>
  <div class="card"><table id="winners">
    <thead><tr><th>Ticker</th><th class="num">Trailing 12m</th>
    <th class="num">Screener composite</th><th class="num">Rank in S&amp;P 500</th></tr></thead>
    <tbody></tbody></table>
    <div class="note sub" style="font-size:.8rem;margin-top:8px">
      Composite/rank from the latest daily scan (sector-neutral, all four
      factors) — a winner with a weak composite is momentum the model
      distrusts; a winner with a strong composite has broad support.</div></div>

  <h2>S&amp;P 500 members ranked by the screener today (top 30)</h2>
  <div class="card"><table id="top30">
    <thead><tr><th>#</th><th>Ticker</th><th>Company</th><th>GICS sector</th>
    <th class="num">Composite</th><th class="num">12-1 mom</th>
    <th class="num">Rank all-market</th></tr></thead><tbody></tbody></table></div>
  <div class="sub" style="font-size:.8rem" id="footer"></div>
</div>
<script>
const D = __PAYLOAD__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pc = v => (v>0?'+':'')+(v*100).toFixed(1)+'%';
const cls = v => v>0?'up':v<0?'down':'';
$('#sub').innerHTML = `Scan of ${D.scan_date} · ${D.n_members} members, ${D.n_scored} with full factor data · <a href="index.html">← screener dashboard</a>`;

const S = D.study;
const persisted = S.mean_spread > 0.005;
$('#answer').innerHTML = `<b>Did last year's winners keep winning?</b>
  In this window (${S.rolling_n} rolling 12-month tests): winners averaged
  <b class="${cls(S.mean_winners)}">${pc(S.mean_winners)}</b> over the following year vs
  <b>${pc(S.mean_median)}</b> for the median member and <b>${pc(S.mean_spy)}</b> for SPY —
  a winners-minus-median spread of <b class="${cls(S.mean_spread)}">${pc(S.mean_spread)}</b>,
  positive in ${(S.hit_rate*100).toFixed(0)}% of tests.
  ${persisted ? 'So yes — persistence showed up, on average, in this period' : 'So no — persistence did not reliably show up in this period'},
  though the year-by-year table shows how uneven it is. Last year's <i>losers</i>
  averaged ${pc(S.mean_losers)}.`;

$('#tiles').innerHTML = [
  [pc(S.mean_spread), 'winners − median, avg next-12m'],
  [(S.hit_rate*100).toFixed(0)+'%', 'tests where winners beat median'],
  [pc(S.mean_winners), 'winners avg next-12m'],
  [pc(S.mean_losers), 'losers avg next-12m'],
  [S.rolling_n, 'rolling monthly tests'],
].map(([v,l])=>`<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

$('#cal tbody').innerHTML = S.calendar.map(c=>`
  <tr><td>${c.anchor}</td>
  <td class="num ${cls(c.winners_fwd)}">${pc(c.winners_fwd)}</td>
  <td class="num ${cls(c.losers_fwd)}">${pc(c.losers_fwd)}</td>
  <td class="num">${pc(c.median_fwd)}</td>
  <td class="num">${pc(c.spy_fwd)}</td>
  <td class="num ${cls(c.winners_fwd-c.median_fwd)}"><b>${pc(c.winners_fwd-c.median_fwd)}</b></td></tr>`).join('')
  + (S.partial ? `<tr><td>${S.partial.anchor} (through ${S.partial.through}, partial)</td>
     <td class="num ${cls(S.partial.winners_fwd)}">${pc(S.partial.winners_fwd)}</td><td class="num">—</td>
     <td class="num">${pc(S.partial.median_fwd)}</td><td class="num">${pc(S.partial.spy_fwd)}</td>
     <td class="num ${cls(S.partial.winners_fwd-S.partial.median_fwd)}"><b>${pc(S.partial.winners_fwd-S.partial.median_fwd)}</b></td></tr>` : '');

(function(){
  const rows = S.calendar; if(!rows.length) return;
  const W=940,H=230,pl=48,pr=10,pt=12,pb=30, groups=rows.length;
  const series=[['winners_fwd','var(--pos)'],['median_fwd','var(--med)'],['spy_fwd','var(--spy)']];
  const all=rows.flatMap(r=>series.map(([k])=>r[k]));
  let lo=Math.min(0,...all), hi=Math.max(0,...all); const span=(hi-lo)||1; lo-=span*.08; hi+=span*.08;
  const y=v=>pt+(hi-v)/(hi-lo)*(H-pt-pb);
  const gw=(W-pl-pr)/groups, bw=Math.min(22,(gw-14)/3);
  let g='';
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,yy=y(v);
    g+=`<line x1="${pl}" x2="${W-pr}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`+
       `<text x="${pl-6}" y="${yy+4}" text-anchor="end">${(v*100).toFixed(0)}%</text>`;}
  g+=`<line x1="${pl}" x2="${W-pr}" y1="${y(0)}" y2="${y(0)}" stroke="var(--axis)"/>`;
  rows.forEach((r,i)=>{
    series.forEach(([k,c],j)=>{
      const x=pl+i*gw+7+j*(bw+2);
      const y0=y(Math.max(0,r[k])), h=Math.abs(y(r[k])-y(0))||1;
      g+=`<rect x="${x}" y="${y0}" width="${bw}" height="${h}" rx="3" fill="${c}"/>`;});
    g+=`<text x="${pl+i*gw+gw/2}" y="${H-8}" text-anchor="middle">${r.anchor.slice(0,4)}</text>`;});
  $('#calchart').innerHTML=`<div class="legend">
    <span><span class="sw" style="background:var(--pos)"></span>prior-year winners</span>
    <span><span class="sw" style="background:var(--med)"></span>median member</span>
    <span><span class="sw" style="background:var(--spy)"></span>SPY</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="Next-12-month returns by formation year">${g}</svg>`;
})();

/* --- top-3 ritual --- */
(function(){
  const T = D.top3; if(!T || !T.rows.length){ return; }
  const money = v => '$' + Number(v).toLocaleString();
  const won = T.final > T.spy_final;
  $('#t3answer').innerHTML = `Over ${T.years} years, ${money(T.start_cash)} became
    <b class="${won?'up':'down'}">${money(T.final)}</b> (${T.cagr}%/yr) following the ritual,
    vs <b>${money(T.spy_final)}</b> (${T.spy_cagr}%/yr) just holding SPY.
    The ritual beat SPY in ${T.beat_years} of ${T.years} years, with a worst
    peak-to-trough drawdown of <b class="down">${T.maxdd}%</b>.
    ${won ? 'It won this window — but read the warning below before believing the dollar figure.'
          : 'Concentration cost it this window: the occasional blow-up year outweighed the wins.'}
    <br><br><b>⚠ Why this number is inflated, badly:</b> the pick pool is
    TODAY'S index members, so any past winner that later collapsed and was
    removed from the index (the strategy's true disasters — think past
    winners that went to zero or faded away) is retroactively erased from
    the simulation. A 3-stock strategy amplifies this survivorship bias more
    than any other design on this site — notice how often the same
    still-famous names recur in the picks. Treat this as an upper fantasy
    bound on the idea, not an expectation. The rolling top-50 study above is
    the more trustworthy read on winner persistence.`;
  $('#t3 tbody').innerHTML = T.rows.map(r=>`
    <tr><td>${r.hold_year}${r.partial ? ` (partial, through ${r.through})` : ''}</td>
    <td>${r.picks.map(p=>`<b>${esc(p.symbol)}</b> (+${p.formation}% → ${p.next_ret===null?'—':(p.next_ret>0?'+':'')+p.next_ret+'%'})`).join(' · ')}</td>
    <td class="num ${cls(r.strat_ret)}">${(r.strat_ret>0?'+':'')+r.strat_ret}%</td>
    <td class="num ${cls(r.spy_ret)}">${(r.spy_ret>0?'+':'')+r.spy_ret}%</td>
    <td class="num">${money(r.value)}</td><td class="num">${money(r.spy_value)}</td></tr>`).join('');
  const rows = T.curve; if(rows.length<2) return;
  const W=940,H=240,pl=56,pr=10,pt=12,pb=30;
  const all=rows.flatMap(r=>[r.strat,r.spy]);
  const lo=Math.log(Math.min(...all))*0.999, hi=Math.log(Math.max(...all))*1.001;
  const x=i=>pl+i/(rows.length-1)*(W-pl-pr);
  const y=v=>pt+(hi-Math.log(v))/(hi-lo)*(H-pt-pb);
  let g='';
  for(let i=0;i<=4;i++){const lv=lo+(hi-lo)*i/4,yy=pt+(hi-lv)/(hi-lo)*(H-pt-pb);
    g+=`<line x1="${pl}" x2="${W-pr}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`+
       `<text x="${pl-6}" y="${yy+4}" text-anchor="end">$${Math.round(Math.exp(lv)/1000)}k</text>`;}
  const path=(k,c)=>`<polyline fill="none" stroke="${c}" stroke-width="2" points="${rows.map((r,i)=>`${x(i)},${y(r[k])}`).join(' ')}"/>`;
  g+=path('spy','var(--spy)')+path('strat','var(--pos)');
  rows.forEach((r,i)=>{if(i%12===0)
    g+=`<text x="${x(i)}" y="${H-8}" text-anchor="middle">${r.date.slice(0,4)}</text>`;});
  $('#t3chart').innerHTML=`<div class="legend">
    <span><span class="sw" style="background:var(--pos)"></span>Top-3 ritual</span>
    <span><span class="sw" style="background:var(--spy)"></span>SPY buy &amp; hold</span>
    <span>log scale</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="Growth of the top-3 strategy vs SPY">${g}</svg>`;
})();

$('#winners tbody').innerHTML = D.cur_winners.map(w=>`
  <tr><td><b>${esc(w.symbol)}</b></td>
  <td class="num up">+${w.trailing_12m}%</td>
  <td class="num">${w.composite===null?'—':(w.composite>0?'+':'')+w.composite.toFixed(2)}</td>
  <td class="num">${w.sp_rank ?? '—'}</td></tr>`).join('');

$('#top30 tbody').innerHTML = D.top30.map(r=>`
  <tr><td class="num">${r.sp_rank}</td><td><b>${esc(r.symbol)}</b></td>
  <td>${esc(r.name)}</td><td>${esc(r.gics)}</td>
  <td class="num">${(r.composite>0?'+':'')+r.composite.toFixed(2)}</td>
  <td class="num ${cls(r.mom_12_1)}">${r.mom_12_1===null?'—':(r.mom_12_1>0?'+':'')+r.mom_12_1+'%'}</td>
  <td class="num">${r.rank_all}</td></tr>`).join('');

$('#footer').textContent = `Generated ${D.generated} · winners/losers = top/bottom ${D.n_group} by trailing 12-month return at each month-end · membership: Wikipedia (current constituents).`;
</script>
</body>
</html>
"""
