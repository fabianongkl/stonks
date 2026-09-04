"""Shared site navigation — one hamburger drawer, injected into every
generated page.  Single source of truth: add a page here and it appears in
the menu everywhere on the next regeneration.

The drawer styles itself with the CSS variables every template already
defines (--surface, --ink, --border, ...), so it follows each page's
light/dark theme for free.
"""

from __future__ import annotations

GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Overview", [
        ("index.html", "Daily Screener", "today's scan, rankings & commentary"),
    ]),
    ("Research", [
        ("backtest.html", "5-Year Backtest", "the methodology vs history"),
        ("sp500.html", "S&P 500 Lens", "index view & winner-persistence"),
    ]),
    ("Paper Books", [
        ("portfolio.html", "Claude's Picks", "$10k · the factor model's own top ten"),
        ("aggressive.html", "Hyper-Aggressive", "$100k · backtest-informed momentum tilt"),
        ("ritual.html", "Top-3 Ritual", "$10k · each January: last year's 3 biggest winners"),
        ("reversal.html", "Loser Reversal", "$10k · each January: last year's 3 worst losers"),
        ("monkey.html", "Monkey Control", "$10k · 10 random picks — the control group"),
        ("insider.html", "Insider Buying", "$10k · follow SEC Form-4 net buying"),
    ]),
]

REPO_URL = "https://github.com/fabianongkl/stonks"


def nav_html(active: str) -> str:
    items = []
    for group, pages in GROUPS:
        items.append(f'<div class="osnav-group">{group}</div>')
        for page, name, blurb in pages:
            cls = "osnav-link active" if page == active else "osnav-link"
            items.append(
                f'<a class="{cls}" href="{page}"><span class="osnav-name">{name}</span>'
                f'<span class="osnav-blurb">{blurb}</span></a>')
    links = "\n".join(items)
    return f"""
<style>
.osnav-btn{{position:fixed;top:12px;right:12px;z-index:60;width:42px;height:42px;
  border-radius:10px;border:1px solid var(--border);background:var(--surface);
  color:var(--ink);font-size:1.15rem;line-height:1;cursor:pointer;
  box-shadow:0 2px 10px rgba(0,0,0,.12)}}
.osnav-btn:hover{{border-color:var(--pos)}}
.osnav-scrim{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:61;
  opacity:0;pointer-events:none;transition:opacity .18s}}
.osnav{{position:fixed;top:0;right:0;bottom:0;width:300px;max-width:85vw;z-index:62;
  background:var(--surface);border-left:1px solid var(--border);
  transform:translateX(102%);transition:transform .2s ease;overflow-y:auto;
  padding:16px 14px 24px;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}}
.osnav.open{{transform:none}}
.osnav-scrim.open{{opacity:1;pointer-events:auto}}
.osnav-title{{font-weight:700;color:var(--ink);font-size:1rem;margin:4px 4px 2px}}
.osnav-sub{{color:var(--muted);font-size:.75rem;margin:0 4px 10px}}
.osnav-group{{color:var(--muted);font-size:.72rem;text-transform:uppercase;
  letter-spacing:.08em;margin:14px 4px 4px}}
.osnav-link{{display:block;padding:8px 10px;border-radius:8px;text-decoration:none;
  margin:2px 0}}
.osnav-link:hover{{background:color-mix(in srgb, var(--ink) 6%, transparent)}}
.osnav-link.active{{background:color-mix(in srgb, var(--pos) 14%, transparent)}}
.osnav-name{{display:block;color:var(--ink);font-weight:600}}
.osnav-blurb{{display:block;color:var(--ink2);font-size:.76rem}}
.osnav-foot{{margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}}
.osnav-foot a{{color:var(--pos);font-size:.8rem;text-decoration:none}}
</style>
<button class="osnav-btn" id="osnav-btn" aria-label="Open site menu"
  aria-expanded="false" aria-controls="osnav">☰</button>
<div class="osnav-scrim" id="osnav-scrim"></div>
<nav class="osnav" id="osnav" aria-label="Site">
  <div class="osnav-title">Open Screener</div>
  <div class="osnav-sub">a public, self-improving factor experiment</div>
  {links}
  <div class="osnav-foot"><a href="{REPO_URL}">Source, data &amp; methodology on GitHub →</a></div>
</nav>
<script>
(function () {{
  const btn = document.getElementById('osnav-btn'),
        nav = document.getElementById('osnav'),
        scrim = document.getElementById('osnav-scrim');
  function set(open) {{
    nav.classList.toggle('open', open);
    scrim.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? '✕' : '☰';
  }}
  btn.addEventListener('click', () => set(!nav.classList.contains('open')));
  scrim.addEventListener('click', () => set(false));
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') set(false); }});
}})();
</script>
"""
