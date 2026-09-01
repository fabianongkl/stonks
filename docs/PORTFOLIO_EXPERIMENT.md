# The portfolio experiment — "Claude's Picks"

A hypothetical **$10,000** portfolio deployed into the screener's top-ranked
stocks on 2026-09-01 and tracked daily thereafter. No real money is involved.

## Why it exists

The screener's rankings are cheap talk until they live inside a portfolio
with fees, concentration, drawdowns and forced decisions. This experiment
converts the screener's daily output into a single, brutally legible number:
did following the model beat doing nothing (buy-and-hold SPY)?

It is deliberately **aggressive**: 10 equal-weight positions is concentrated
enough that factor bets actually show up in the results, rather than being
diversified into oblivion. That concentration raises variance — which is
priced into the expectations below.

## The rules (mechanical, no discretion)

1. **Selection:** the top 10 stocks by composite rank among those with full
   factor coverage, equal-weighted at ~$1,000 each, whole shares only;
   residual cash stays cash.
2. **Fees:** IBKR Fixed commission model on every trade — $0.005/share,
   minimum $1, maximum 1% of trade value. (Regulatory pass-through fees on
   sells, fractions of a cent per share, are ignored as immaterial.)
3. **Holding rule:** positions are reviewed **monthly** (`--review`). A
   holding is sold only if its composite rank has decayed past **#50** among
   full-coverage stocks; the proceeds buy the best-ranked stock not already
   held. No stops, no daily trading, no market timing.
4. **Sector cap (amendment, 2026-09-01, same day as inception):** any *new*
   selection skips stocks whose sector already holds 2 positions, so the book
   stays a set of stock picks rather than one sector bet. The methodology
   became sector-aware the same day (METHODOLOGY.md changelog v0.2); the
   original ten holdings are grandfathered and simply age out through the
   normal rank-decay rule.
5. **Benchmark:** SPY buy-and-hold from inception, same start value.

Why this strategy and not day-trading or options:

- The screener's evidence base (see METHODOLOGY.md) is about **1–6 month**
  cross-sectional stock selection. Daily trading would be betting on a signal
  the model does not possess, and would drown the experiment in fees.
- Options add leverage, path-dependency and IV pricing — they would test
  volatility timing, not the screener. Wrong instrument for this hypothesis.
- Monthly-with-tolerance rebalancing keeps turnover (and fees) low while
  still following the model when its opinion genuinely changes — the same
  logic factor funds use.

## Honest expectations (set on day one, before any results)

- A 10-stock portfolio of this profile has annualised volatility around
  20–25%. Over 3 months, swings of ±10–12% are one standard deviation —
  **normal**, not signal.
- If the factor edge is real and average-sized (the literature's few percent
  a year of long-decile spread), the *expected* edge over SPY is roughly
  0.5–1.5% per quarter — small enough that a year of data will still be
  statistically ambiguous. The experiment's honest deliverable is the
  *accumulating record*, not a quick verdict.
- Any single pick is barely better than a coin flip (mean IC of a good
  factor ≈ 0.03–0.05). The portfolio, not the pick, is the unit of account.

## The second book: "Hyper-Aggressive" (added 2026-09-01, same day)

A **$100,000** paper book run alongside the core book as a live A/B test.
Rules (mechanical, like everything here):

1. **8 positions**, equal-weighted at inception, whole shares.
2. **Its own ranking:** momentum 50%, low-volatility 20%, quality 20%,
   value 10% — openly informed by the 5-year backtest's factor ICs. That is
   the experiment: the core book trusts the literature's priors; this book
   follows the backtest. The live record decides which philosophy was right.
3. **Faster rotation:** monthly review sells a holding whose rank (on this
   book's own scoring) decays past **#100**; sector cap of 2 applies to all
   replacements.
4. **No leverage, no options.** Simulating margin calls and option IV
   without real borrow/quote data would put fantasy numbers on the permanent
   record. Aggression = concentration + tilt + turnover, honestly costed
   (IBKR commissions).
5. Same benchmark (SPY), same ledger tables (`book` column), own dashboard
   page (`aggressive.html`).

Deployed 2026-09-01: FLXS, MAAS, CBL, TD, PAGP, APLE, DAC, ALTO at ~$12.5k
each; $29.88 fees; $184.62 residual cash.

## Daily workflow

```
python run_scan.py          # the screener records today's market
python track_portfolio.py   # mark portfolio to market, update dashboard/portfolio.html
```

First business day of each month, additionally:

```
python track_portfolio.py --review
```

All transactions, snapshots and their reasons live in the same SQLite
database as the scans (`pf_txns`, `pf_snapshots`) — one permanent record.
