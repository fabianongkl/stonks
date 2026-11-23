# Project handoff: state, decisions, and how to continue

First written 2026-09-04, when day-to-day stewardship moved from a local
Claude Code session to a claude.ai Project. Updated 2026-11-24. The October
and November reviews were run from a local Claude Code session; either route
works. Everything an assistant (or human) needs to continue is in this repo
or at the public URLs below.

## What this project is

An open-source, self-improving daily stock screener plus six hypothetical
paper books, run as a public experiment: can an AI-maintained factor model
predict stock performance, and can it learn from its record? Not investment
advice; the record's honesty is the product. Started 2026-09-01.

## Live URLs (everything is public)

| Thing | URL |
|---|---|
| Dashboard (daily scan, sector commentary) | https://fabianongkl.github.io/stonks/ |
| Core book "Claude's Picks" ($10k) | https://fabianongkl.github.io/stonks/portfolio.html |
| Hyper-Aggressive book ($100k) | https://fabianongkl.github.io/stonks/aggressive.html |
| Top-3 Ritual book ($10k) | https://fabianongkl.github.io/stonks/ritual.html |
| Loser Reversal book ($10k) | https://fabianongkl.github.io/stonks/reversal.html |
| Monkey Control book ($10k) | https://fabianongkl.github.io/stonks/monkey.html |
| Insider Buying book ($10k) | https://fabianongkl.github.io/stonks/insider.html |
| S&P 500 Lens | https://fabianongkl.github.io/stonks/sp500.html |
| 5-year backtest | https://fabianongkl.github.io/stonks/backtest.html |
| Machine-readable daily payloads | https://fabianongkl.github.io/stonks/data/YYYY-MM-DD.json |
| Repo (code, database, journal, all committed daily) | https://github.com/fabianongkl/stonks |

## What runs without anyone

GitHub Actions (`.github/workflows/daily-scan.yml`), cron 03:00 UTC Tue-Sat
(the night of each Mon-Fri session). GitHub starts it late: recent runs began
about 09:40-11:40 UTC, still before the US open at 14:30 UTC. Steps:
`run_scan.py` (scan, score, outcomes) -> `track_portfolio.py` (snapshot all
six books, January and quarterly auto-rotations) -> `run_sp500.py` -> commit
`data/screener.db`, `journal/`, `dashboard/` -> deploy Pages. A second
workflow (`deploy-pages.yml`) redeploys Pages on every push.
**No AI is involved in the daily run**; commentary is rule-generated.

Data-quality guards fail the run loudly (red X, nothing committed) rather
than record a degraded scan: under 60% price coverage, or a scan shrinking
an existing same-date scan by over 20%. Each scan is labelled by its price
date (the date most symbols' data ends on) and the panel is cut there. A
second scan of the same price date archives the first instead of deleting it.

## Where things stand (2026-11-20 close)

- 50 daily scans, 2026-08-31 to 2026-11-20. The latest scored 3,288 stocks,
  2,905 eligible (factor completeness gate). Methodology v0.3 (total return).
- 21-day evaluations: 31 matured scans, mean top-decile spread over the
  median +1.57%, 21 positive. The windows overlap, so this is about one
  independent observation. First 63-day result: about Nov 27 to 30.
- Weights are still the defaults (value 0.25, quality 0.30, momentum 0.30,
  low-vol 0.15). Adaptation needs 6 non-overlapping 63-day windows (about
  early 2028), by design; see METHODOLOGY.md.
- Books at the 2026-11-20 close (SPY total return: -9.85% since 2026-08-31,
  -10.17% since 2026-09-03):

  | book | started | value | P&L | vs SPY |
  |---|---|---|---|---|
  | Claude's Picks (core) | 2026-08-31 | $9,574 | -4.3% | +5.6 |
  | Loser Reversal | 2026-09-03 | $9,601 | -4.0% | +6.2 |
  | Hyper-Aggressive | 2026-08-31 | $88,355 | -11.6% | -1.8 |
  | Monkey Control | 2026-09-03 | $8,691 | -13.1% | -2.9 |
  | Insider Buying | 2026-09-03 | $6,561 | -34.4% | -24.2 |
  | Top-3 Ritual | 2026-09-03 | $4,083 | -59.2% | -49.0 |

- Incidents on the record: Sep 1 throttled run (restored from git history,
  retries and guards added; if runs go red repeatedly, the queued fix is a
  Stooq price fallback, see docs/DATA_SOURCES.md). Sep to Nov: cloud runs
  that started 00:00-01:40 UTC got the previous day's bars; fixed by the
  schedule move, `scans.price_asof`, and the 2026-11-24 relabel and dedupe.

## Standing decisions (do not silently reverse)

1. **The backtest never sets live weights** (results are survivorship-biased;
   tuning to them manufactures overfitting). It measured: top decile
   +6.5%/yr vs median (t=1.94), ICs low-vol +0.088 / momentum +0.043 /
   quality 0 / value -0.018, sim book lagged SPY.
2. **Core vs Hyper-Aggressive is an A/B test**: core follows literature
   priors, aggressive follows a backtest-informed tilt. Monkey Control is the
   random control group. Don't homogenize them.
3. **Every methodology change needs a written journal entry first**
   (docs/REVIEW_PROTOCOL.md). One structural change at a time.
4. **No leverage/options in the paper books**: unsimulatable honestly.
5. **Never delete history**; bad calls stay on the record. Duplicates and bad
   scans are archived (`scans_superseded`, `scores_superseded`), not deleted.
6. **Total return everywhere** (v0.3): outcomes, books and the SPY benchmark
   include dividends and splits. Price-only figures are archived.
7. **Price date drives the arithmetic.** `scans.price_asof` sets every window
   and horizon; scan labels equal it. Pick the latest scan by
   `scan_date DESC`, never `MAX(scan_id)` (ids follow insertion order).
8. **Never run a scan, `track_portfolio.py` or any live-price step locally
   during the US session** (14:30-21:00 UTC). It records intraday prices.
9. **Factor completeness gate**: a factor counts only if at least half its
   metrics are present. Use `factors.eligible_mask()`, never
   `n_factors == 4`.
10. **Turnover hysteresis**: a holding sells after 2 consecutive reviews
    beyond tolerance (`REVIEW_STRIKES = 2`); universe dropouts sell at once.
11. **Book rotations stay on schedule**: no off-schedule rebuilds (attribution
    integrity). Insider rotates quarterly; Ritual, Reversal and Monkey each
    January.
12. **USD facts only** until there is an FX layer; non-USD reporters stay
    unscored.

## Open items for the December review (target: first days of December)

Updated 2026-11-24. Sources: `journal/review-2026-11.md` (agenda and
addenda) and `journal/review-2026-10.md`. The October open items were all handled in those reviews.

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

## How to run the monthly review

Inputs are fetchable URLs, so a chat-only assistant can do the analysis:

1. Read the latest `journal/*.md`, the previous `journal/review-YYYY-MM.md`
   and `dashboard/data/<latest>.json` (raw.githubusercontent.com or the
   Pages URLs above).
2. Follow docs/REVIEW_PROTOCOL.md's checklist and the open items above.
3. Write the review as `journal/review-YYYY-MM.md`.
4. Portfolio trades: run `python track_portfolio.py --review` on a machine,
   outside US market hours (rule 8). The cloud job only snapshots.
5. One-time data corrections go in `scripts/` as idempotent, documented
   migrations (see the 2026-11 scripts), with a journal entry.

## Repo map

Start with README.md -> docs/METHODOLOGY.md (every metric justified, plus
changelog) -> docs/PORTFOLIO_EXPERIMENT.md (book rules) ->
docs/DATA_SOURCES.md (sources and limitations) -> the latest
`journal/review-*.md` -> backtest/results/REPORT.md (read its caveats).
Shelved research: docs/PGM_SCREEN.md and docs/PEAD_SCREEN.md (backtests in
`backtest/pgm/`). The user's background: beginner student, values honest
odds tables when trades are discussed, keeps this project strictly separate
from their other projects.
