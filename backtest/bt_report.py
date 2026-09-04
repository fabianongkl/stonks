"""Backtest outputs: results markdown + self-contained HTML chart page."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from screener import config as live_config
from screener import nav
from . import config_bt

log = logging.getLogger("bt.report")


def _ann(monthly: pd.Series) -> float:
    m = monthly.dropna()
    if m.empty:
        return float("nan")
    return float((1 + m).prod() ** (12 / len(m)) - 1)


def _maxdd(curve: pd.Series) -> float:
    peak = curve.cummax()
    return float((curve / peak - 1).min())


def summarize(res: pd.DataFrame, facts: pd.DataFrame,
              priced_by_year: dict[int, int]) -> dict:
    res = res.copy()
    res["date"] = pd.to_datetime(res["date"])
    spread = res["spread"]
    tstat = float(spread.mean() / spread.std(ddof=1) * np.sqrt(len(spread))) \
        if len(spread) > 2 else float("nan")

    top_curve = (1 + res["top_decile"]).cumprod()
    med_curve = (1 + res["median_fwd"]).cumprod()
    spy_curve = (1 + res["spy_fwd"].fillna(0)).cumprod()
    book_curve = res["book_value"] / 10_000.0

    # survivorship quantification: filers with an annual fact ending in year Y
    ann = facts[(facts["dur_days"].between(330, 400))].copy()
    ann["year"] = ann["end"].str[:4].astype(int)
    filers_by_year = ann.groupby("year")["cik"].nunique().to_dict()

    ic_cols = [c for c in res.columns if c.startswith("ic_")]
    return {
        "months": len(res),
        "period": f"{res['date'].iloc[0].date()} .. {res['date'].iloc[-1].date()}",
        "ann_top": _ann(res["top_decile"]),
        "ann_median": _ann(res["median_fwd"]),
        "ann_bottom": _ann(res["bottom_decile"]),
        "ann_spy": _ann(res["spy_fwd"]),
        "ann_spread": _ann(res["top_decile"]) - _ann(res["median_fwd"]),
        "spread_tstat": tstat,
        "spread_hit_rate": float((spread > 0).mean()),
        "book_final": float(res["book_value"].iloc[-1]),
        "book_maxdd": _maxdd(book_curve),
        "spy_maxdd": _maxdd(spy_curve),
        "top_maxdd": _maxdd(top_curve),
        "mean_ics": {c[3:]: float(res[c].mean()) for c in ic_cols},
        "coverage": [
            {"year": int(y),
             "filers_with_annual": int(filers_by_year.get(y, 0)),
             "priced_symbols": int(priced_by_year.get(y, 0))}
            for y in sorted(set(list(filers_by_year) + list(priced_by_year)))
            if 2020 <= y <= 2026],
        "monthly": [
            {"date": d.strftime("%Y-%m"), "top": round(t * 100, 2),
             "med": round(m * 100, 2), "spread": round(s * 100, 2),
             "spy": round(0 if pd.isna(y_) else y_ * 100, 2),
             "book": round(b, 0)}
            for d, t, m, s, y_, b in zip(res["date"], res["top_decile"],
                                         res["median_fwd"], res["spread"],
                                         res["spy_fwd"], res["book_value"])],
    }


def write_report(s: dict) -> str:
    lines = [
        "# Backtest report — plumbing & behavior test",
        "",
        f"Generated {datetime.now().isoformat(timespec='seconds')} · "
        f"{s['months']} monthly rebalances · {s['period']} · "
        f"fixed v0.2 default weights (no learning, no tuning).",
        "",
        "## READ THIS FIRST — what these numbers are and are not",
        "",
        "- **Survivorship-biased.** The universe is today's listings; companies",
        "  that delisted during the window are absent. This inflates all",
        "  returns below, plausibly by 1–4%/yr, worst in small-cap value.",
        "  The coverage table quantifies the visible gap.",
        "- **Point-in-time fundamentals** (EDGAR filing dates respected), so",
        "  no look-ahead on financials — but prices are survivor prices.",
        "- **These numbers never set the live weights.** They validate the",
        "  machinery and set expectations; the live record is the experiment.",
        "",
        "## Headline (biased — see above)",
        "",
        "| Portfolio | Annualized |",
        "|---|---|",
        f"| Top decile (composite) | {s['ann_top']*100:+.1f}% |",
        f"| Median scored stock | {s['ann_median']*100:+.1f}% |",
        f"| Bottom decile | {s['ann_bottom']*100:+.1f}% |",
        f"| SPY | {s['ann_spy']*100:+.1f}% |",
        f"| **Top-decile spread vs median** | **{s['ann_spread']*100:+.1f}%/yr** |",
        "",
        f"Spread t-stat: **{s['spread_tstat']:.2f}** · monthly hit rate "
        f"{s['spread_hit_rate']*100:.0f}% · max drawdowns: top decile "
        f"{s['top_maxdd']*100:.0f}%, SPY {s['spy_maxdd']*100:.0f}%.",
        "",
        "## Simulated Claude's-Picks book (10 stocks, live rules, fees)",
        "",
        f"$10,000 → **${s['book_final']:,.0f}** · max drawdown "
        f"{s['book_maxdd']*100:.0f}%.",
        "",
        "## Factor ICs (mean Spearman rank-corr with next-month returns)",
        "",
        "| Factor | Mean IC |",
        "|---|---|",
    ]
    for f, v in s["mean_ics"].items():
        lines.append(f"| {f} | {v:+.4f} |")
    lines += [
        "",
        "## Coverage / survivorship quantification",
        "",
        "| Year | SEC filers w/ annual report | Symbols with prices |",
        "|---|---|---|",
    ]
    for c in s["coverage"]:
        lines.append(f"| {c['year']} | {c['filers_with_annual']} |"
                     f" {c['priced_symbols']} |")
    lines += [
        "",
        "The widening gap in early years = companies that have since delisted",
        "or changed tickers, invisible to this backtest. That absence is the",
        "survivorship bias, in units of missing companies.",
        "",
        "## Monthly detail",
        "",
        "| Month | Top decile | Median | Spread | SPY | Book |",
        "|---|---|---|---|---|---|",
    ]
    for m in s["monthly"]:
        lines.append(f"| {m['date']} | {m['top']:+.1f}% | {m['med']:+.1f}% "
                     f"| {m['spread']:+.1f}% | {m['spy']:+.1f}% | ${m['book']:,.0f} |")
    out = config_bt.RESULTS / "REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def write_html(s: dict) -> str:
    html = (_TEMPLATE
            .replace("__NAV__", nav.nav_html("backtest.html"))
            .replace("__PAYLOAD__", json.dumps(s)))
    out = live_config.DASHBOARD_DIR / "backtest.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screener Backtest</title>
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
.tile .v{font-size:1.4rem;font-weight:650}.tile .l{color:var(--ink2);font-size:.8rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px;overflow-x:auto}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--ink2);font-size:.8rem;margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}
svg text{fill:var(--ink2);font:11px system-ui,sans-serif}
.disclaimer{border:1px solid var(--border);border-left:3px solid var(--warn);
  border-radius:8px;padding:10px 14px;margin:22px 0;color:var(--ink2);font-size:.85rem}
a{color:var(--pos)}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);
  border:1px solid var(--border);border-radius:8px;padding:8px 10px;
  font-size:.8rem;box-shadow:0 4px 14px rgba(0,0,0,.18);display:none;z-index:9}
</style>
</head>
<body>
__NAV__
<div class="wrap">
  <h1>Backtest — plumbing &amp; behavior test</h1>
  <div class="sub" id="sub"><a href="index.html">← screener dashboard</a></div>

  <div class="disclaimer"><b>Survivorship-biased by construction</b> — the
  universe is today's listings, so companies that died during the window are
  missing and every number here is flattered, plausibly by 1–4%/yr. Fundamentals
  are point-in-time (no look-ahead). These results validate the machinery and
  set expectations; they never set the live weights. Full caveats in
  backtest/results/REPORT.md.</div>

  <div class="tiles" id="tiles"></div>

  <h2>Growth of $1 (log scale)</h2>
  <div class="card" id="curves"></div>

  <h2>Monthly top-decile spread vs scored median</h2>
  <div class="card" id="spread"></div>
</div>
<div class="tooltip" id="tip"></div>
<script>
const D = __PAYLOAD__;
const $ = s => document.querySelector(s);
$('#sub').innerHTML = `${D.months} monthly rebalances · ${D.period} · fixed v0.2 weights · <a href="index.html">← screener dashboard</a>`;
const pct = v => (v>0?'+':'')+(v*100).toFixed(1)+'%';
$('#tiles').innerHTML = [
  [pct(D.ann_top),'top decile / yr'],
  [pct(D.ann_median),'median stock / yr'],
  [pct(D.ann_spy),'SPY / yr'],
  [`<b>${pct(D.ann_spread)}</b>`,'spread / yr (biased)'],
  [D.spread_tstat.toFixed(2),'spread t-stat'],
  ['$'+Math.round(D.book_final).toLocaleString(),'sim book from $10k'],
].map(([v,l])=>`<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

(function(){
  const M = D.monthly; if(!M.length) return;
  let top=1, med=1, spy=1; const rows=[];
  M.forEach(m=>{ top*=1+m.top/100; med*=1+m.med/100; spy*=1+m.spy/100;
                 rows.push({d:m.date, top, med, spy, book:m.book/10000}); });
  const W=940,H=260,pl=46,pr=10,pt=12,pb=30;
  const all=rows.flatMap(r=>[r.top,r.med,r.spy,r.book]);
  const lo=Math.log(Math.min(...all))*1.05, hi=Math.log(Math.max(...all))*1.05;
  const x=i=>pl+i/(rows.length-1)*(W-pl-pr);
  const y=v=>pt+(hi-Math.log(v))/(hi-lo)*(H-pt-pb);
  let g='';
  for(let i=0;i<=4;i++){const lv=lo+(hi-lo)*i/4, yy=pt+(hi-lv)/(hi-lo)*(H-pt-pb);
    g+=`<line x1="${pl}" x2="${W-pr}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`+
       `<text x="${pl-6}" y="${yy+4}" text-anchor="end">${Math.exp(lv).toFixed(2)}</text>`;}
  const path=(k,c)=>`<polyline fill="none" stroke="${c}" stroke-width="2" points="${rows.map((r,i)=>`${x(i)},${y(r[k])}`).join(' ')}"/>`;
  g+=path('med','var(--med)')+path('spy','var(--spy)')+path('book','var(--neg)')+path('top','var(--pos)');
  rows.forEach((r,i)=>{if(i%Math.ceil(rows.length/10)===0)
    g+=`<text x="${x(i)}" y="${H-8}" text-anchor="middle">${r.d}</text>`;});
  $('#curves').innerHTML=`<div class="legend">
    <span><span class="sw" style="background:var(--pos)"></span>top decile</span>
    <span><span class="sw" style="background:var(--neg)"></span>sim 10-stock book</span>
    <span><span class="sw" style="background:var(--spy)"></span>SPY</span>
    <span><span class="sw" style="background:var(--med)"></span>median stock</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Cumulative growth, log scale">${g}</svg>`;
})();

(function(){
  const M=D.monthly; if(!M.length) return;
  const W=940,H=200,pl=46,pr=10,pt=10,pb=30;
  let lo=Math.min(0,...M.map(m=>m.spread)), hi=Math.max(0,...M.map(m=>m.spread));
  const span=(hi-lo)||1; lo-=span*.08; hi+=span*.08;
  const y=v=>pt+(hi-v)/(hi-lo)*(H-pt-pb);
  const bw=Math.max(3,(W-pl-pr)/M.length-2);
  let g='';
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,yy=y(v);
    g+=`<line x1="${pl}" x2="${W-pr}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`+
       `<text x="${pl-6}" y="${yy+4}" text-anchor="end">${v.toFixed(1)}%</text>`;}
  g+=`<line x1="${pl}" x2="${W-pr}" y1="${y(0)}" y2="${y(0)}" stroke="var(--axis)"/>`;
  M.forEach((m,i)=>{
    const xx=pl+2+i*((W-pl-pr-4)/M.length);
    const y0=y(Math.max(0,m.spread)),h=Math.abs(y(m.spread)-y(0))||1;
    g+=`<rect class="b" data-i="${i}" x="${xx}" y="${y0}" width="${bw}" height="${h}" rx="1.5"
        fill="${m.spread>=0?'var(--pos)':'var(--neg)'}"/>`;
    if(i%Math.ceil(M.length/10)===0)
      g+=`<text x="${xx}" y="${H-8}">${m.date}</text>`;});
  $('#spread').innerHTML=`<div class="legend">
    <span><span class="sw" style="background:var(--pos)"></span>picks beat median</span>
    <span><span class="sw" style="background:var(--neg)"></span>picks lagged</span>
    <span>hit rate ${(D.spread_hit_rate*100).toFixed(0)}%</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Monthly spread">${g}</svg>`;
  const tip=$('#tip');
  document.querySelectorAll('.b').forEach(b=>{
    b.addEventListener('mousemove',e=>{const m=M[+b.dataset.i];
      tip.style.display='block';tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY-10)+'px';
      tip.innerHTML=`<b>${m.date}</b><br>top: ${m.top>0?'+':''}${m.top}%<br>median: ${m.med>0?'+':''}${m.med}%<br><b>spread: ${m.spread>0?'+':''}${m.spread}%</b><br>SPY: ${m.spy>0?'+':''}${m.spy}%`;});
    b.addEventListener('mouseleave',()=>tip.style.display='none');});
})();
</script>
</body>
</html>
"""
