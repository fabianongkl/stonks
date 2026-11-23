# Project handoff — state, decisions, and how to continue

Written 2026-09-04, at the point where day-to-day stewardship moves from a
local Claude Code session to a claude.ai Project. Everything an assistant
(or human) needs to continue is in this repo or at the public URLs below —
no local files required.

## What this project is

An open-source, self-improving daily stock screener plus two hypothetical
portfolios, run as a public experiment: can an AI-maintained factor model
predict stock performance, and can it learn from its record? Not investment
advice; the record's honesty is the product. Started 2026-09-01.

## Live URLs (everything is public)

| Thing | URL |
|---|---|
| Dashboard (daily scan, sector commentary) | https://fabianongkl.github.io/stonks/ |
| Core book "Claude's Picks" ($10k) | https://fabianongkl.github.io/stonks/portfolio.html |
| Hyper-Aggressive book ($100k) | https://fabianongkl.github.io/stonks/aggressive.html |
| 5-year backtest | https://fabianongkl.github.io/stonks/backtest.html |
| Machine-readable daily payloads | https://fabianongkl.github.io/stonks/data/YYYY-MM-DD.json |
| Repo (code, database, journal — all committed daily) | https://github.com/fabianongkl/stonks |

## What runs without anyone

GitHub Actions (`.github/workflows/daily-scan.yml`), weekdays 21:30 UTC:
scan → score → snapshot both books → commit `data/screener.db`, `journal/`,
`dashboard/` → deploy Pages. A second workflow redeploys Pages on every push.
**No AI is involved in the daily run** — commentary is rule-generated.
Data-quality guards fail the run loudly (red X, nothing committed) rather
than record a degraded scan (<60% price coverage, or shrinking an existing
same-date scan >20%).

## Where things stand (2026-09-04)

- 3 clean daily scans recorded (Sep 1–3), ~3,460 stocks scored each,
  ~94% full factor coverage.
- Core book: $10,082.70 (+0.8%). Aggressive book: $100,571.98 (+0.6%).
- No outcomes have matured yet. First 21-day evaluations: ~Sep 30.
  First possible weight adaptation: needs 6 non-overlapping 63-day windows
  (≈ early 2028) — by design, see METHODOLOGY.md.
- Known incident: Sep 1's first cloud run was Yahoo-throttled and briefly
  corrupted the record; restored from git history; hardening added.
  If daily runs go red repeatedly, the queued fix is a Stooq price fallback
  for the runner (see docs/DATA_SOURCES.md).

## Standing decisions (do not silently reverse)

1. **The backtest never sets live weights** (results are survivorship-biased;
   tuning to them manufactures overfitting). It measured: top decile
   +6.5%/yr vs median (t=1.94), ICs low-vol +0.088 / momentum +0.043 /
   quality 0 / value −0.018, sim book lagged SPY.
2. **The two books are an A/B test**: core follows literature priors,
   aggressive follows backtest-informed tilt. Don't homogenize them.
3. **Every methodology change needs a written journal entry first**
   (docs/REVIEW_PROTOCOL.md). One structural change at a time.
4. **No leverage/options in the paper books** — unsimulatable honestly.
5. **Never delete history**; bad calls stay on the record.

## Open items for the December review (target: first days of December)

Updated 2026-11-24. Sources: `journal/review-2026-11.md` (agenda and
addenda) and `journal/review-2026-10.md`. The October items above this
point were all handled in those reviews.

1. **First 63-day maturity.** Scan 2026-08-31 matures on the first scan
   after 2026-11-26 (Thanksgiving week, so about Nov 27 to 30). Report the
   decile spread, bootstrap p-value and factor ICs at 63d. Still roughly one
   independent observation; say so.
2. **IFRS vs US-GAAP cohort.** IFRS names have 21-day outcomes from about
   2026-11-17. Measure factor IC separately for each cohort.
3. **Momentum IC reversal** (-0.16 on October formations). Record whether it
   persists or reverts. Do not react.
4. **Confirm the cloud runs on clean labels.** Every scan should have
   `scan_date == price_asof`, no weekend or duplicate labels, no missing SPY
   prints, and no "cut back" warnings. Note: the cron says 03:00 UTC
   Tue-Sat, but GitHub has been starting the job at about 09:40-11:40 UTC.
   That is still before the US open (14:30 UTC); if starts drift past it,
   the scan would record intraday prices.
5. **Dropout marking.** Holdings that leave the scan universe are marked at
   a live quote taken when the job runs. Consider marking them at the
   scan's price date instead, so a late or manual run cannot record an
   intraday price.
6. **Monthly review trades.** Automate `track_portfolio.py --review` in the
   workflow, or keep it an operator step (open since October).
7. **FX layer.** About 90 non-USD reporters (42 CAD, 21 EUR) stay unscored.
   Decide whether an FX layer is worth building (open since October).

Context for reading the record (no action needed):

- 2026-10-16 is now scan 48 (true close), scored the night before IFRS
  support shipped, so its ranks are the v0.2.1 model's. The 2026-10-16
  portfolio snapshots still carry intraday marks from the Oct 17 re-run.
- 21-day record after the dedupe: 31 matured scans, mean top-decile spread
  +1.57%, 21 positive.

Done since the November review:

- Proposals A (total return) and B (split-adjusted issuance) shipped as
  v0.3 on 2026-11-05.
- Relabel and dedupe applied on 2026-11-24 (commit 69831d6,
  `scripts/2026-11_dedupe_and_relabel_by_price_asof.py`): scans 27, 35, 53
  and 60 archived as duplicate days, scan 48 canonical for Oct 16 in place
  of intraday scan 47, 17 scans relabelled. Every scan label now equals its
  price date.

## How to run the monthly review from a chat-only assistant

All inputs are fetchable URLs; no local execution needed:

1. Read the latest `journal/*.md` and `dashboard/data/<latest>.json`
   (raw.githubusercontent.com or the Pages URLs above).
2. Follow docs/REVIEW_PROTOCOL.md's checklist.
3. Write the review as `journal/review-YYYY-MM.md` (commit via github.com
   web editor, a Claude GitHub integration, or hand the file to the user).
4. Portfolio trades: run `python track_portfolio.py --review` — needs a
   machine. Either the user runs it locally (repo README), or add a
   `workflow_dispatch` input to trigger it in Actions (small change,
   documented intent).

## Repo map

Start with README.md → docs/METHODOLOGY.md (every metric justified +
changelog) → docs/PORTFOLIO_EXPERIMENT.md (book rules) →
docs/DATA_SOURCES.md (sources + limitations) → backtest/results/REPORT.md
(read its caveats). The user's background: beginner student, values honest
odds tables when trades are discussed, keeps this project strictly separate
from their other projects.
