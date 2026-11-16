# PEAD run log: 2026-11-16

Rules: [docs/PEAD_SCREEN.md](../../../docs/PEAD_SCREEN.md), frozen before the
event data was downloaded. Code: `backtest/pgm/fetch_events.py`,
`backtest/pgm/pead.py` (reuses the PGM price panels, universe filters, cost
model and metrics). Separate from the PGM screen, whose rules and results are
unchanged. Research only: no orders, no live weights.

## What was run

1. Rules written to `docs/PEAD_SCREEN.md`.
2. `python -m backtest.pgm.fetch_events`: EDGAR `submissions` for 5,229
   companies plus older history files, <= 8 requests/s, generic User-Agent.
   ~31 minutes.
3. Dry run on the first ~500 companies to check the code. With so few events
   the 200-event minimum blocked every signal, so the test harness (not the
   rules) lowered it to 20 for that dry run only. No rule changed.
4. `python -m backtest.pgm.pead` (log: `pead_engine_2026-11-16.log`).
5. Alignment checks (below).

## Data

- 128,350 earnings events mapped to universe symbols (108,964 Item 2.02
  8-Ks, 19,386 10-Q/10-K fallbacks). 70,455 passed the universe filters at
  day 0 from 2013-03 onward. 7,216 top-decile signals (4,902 development,
  2,314 holdout).
- **Alignment check:** mean absolute 3-day abnormal return is 6.9% at day 0,
  6.6-6.8% at one day either side, and 2.6-2.8% at 3-10 days away, so event
  days are placed correctly. Example: NVDA's 2023-05-24 after-close report,
  day 0 = 2023-05-25, reaction +25.5% vs a 11.7% cut-off: a top signal.
- Same survivorship bias as the PGM run (only 34% of 2013 EDGAR revenue
  filers are priced). For PEAD the bias mostly affects the level of returns,
  less the event-level comparison.

## Diagnostic: does drift exist here?

Average 60-trading-day return after entry, minus SPY, by reaction decile
(deciles within each calendar quarter):

| Decile (1 = worst reaction) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Development (~4,800 each) | +1.3% | -0.3% | -0.1% | -0.4% | -0.1% | -0.2% | -0.6% | -0.0% | -0.3% | -0.6% |
| Holdout (~2,240 each) | -1.9% | -1.5% | -1.9% | -0.1% | -1.9% | -1.8% | -1.8% | -1.8% | -1.2% | +0.1% |

Traded top-decile signals: mean -0.4% (median -1.4%) in development and
+0.1% (median -2.5%) in holdout. **There is no drift in this universe.** In
development the pattern even tilts slightly toward reversal (worst reactions
did best). This matches recent research arguing that PEAD in liquid US
stocks has largely disappeared since the 2000s (e.g. Martineau 2022).

## Results ($3,000 start per period)

Every book's development drawdown was deeper than SPY's, so the
development-fixed SPY/SHY weight is 1.00 (plain SPY) for all of them. That
weight was then used unchanged in holdout and full.

### Development (2013-03-01 to 2023-05-30)

| Book | CAGR | Worst DD | Ret/DD | End $ | Qtrs beat SPY | Hit rate | Top 1% of P&L | Top 5% of P&L | Turnover/yr | Fees $ (%/yr) | Orders |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 12.5% | -33.7% | 0.37 | 9,993 | | | | | | | |
| EW universe | 11.0% | -40.7% | 0.27 | 8,772 | | | | | | | |
| 20 slots ibkr | 3.2% | -49.2% | 0.07 | 4,160 | 38% | 52% | 150% | 387% | 360% | 1,756 (3.9%) | 1,636 |
| 20 slots uncon | 7.8% | -50.1% | 0.16 | 6,446 | 48% | 55% | 71% | 191% | 397% | 240 (0.4%) | 1,632 |
| 8 slots ibkr | 5.3% | -49.6% | 0.11 | 5,088 | 43% | 55% | 114% | 284% | 383% | 804 (1.7%) | 664 |
| 8 slots uncon | 7.5% | -51.2% | 0.15 | 6,268 | 45% | 56% | 80% | 211% | 398% | 220 (0.4%) | 664 |
| All signals | 9.5% | -49.9% | 0.19 | 7,617 | 53% | n/a | n/a | n/a | 471% | 300 (0.5%) | n/a |

### Holdout (2023-05-31 to 2026-11-13)

| Book | CAGR | Worst DD | Ret/DD | End $ | Qtrs beat SPY | Hit rate | Top 1% of P&L | Top 5% of P&L | Turnover/yr | Fees $ (%/yr) | Orders |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 20.1% | -18.8% | 1.07 | 5,657 | | | | | | | |
| EW universe | 15.0% | -25.5% | 0.59 | 4,858 | | | | | | | |
| 20 slots ibkr | 3.4% | -36.6% | 0.09 | 3,368 | 36% | 48% | 100% | 313% | 378% | 609 (6.1%) | 574 |
| 20 slots uncon | 10.6% | -33.0% | 0.32 | 4,246 | 43% | 52% | 45% | 144% | 416% | 50 (0.4%) | 580 |
| 8 slots ibkr | 12.9% | -30.3% | 0.43 | 4,566 | 43% | 55% | 35% | 87% | 399% | 274 (2.5%) | 238 |
| 8 slots uncon | 16.4% | -29.2% | 0.56 | 5,077 | 36% | 54% | 31% | 76% | 426% | 52 (0.4%) | 240 |
| All signals | 17.5% | -32.5% | 0.54 | 5,232 | 43% | n/a | n/a | n/a | 527% | 70 (0.5%) | n/a |

### Full period (2013-03-01 to 2026-11-13)

| Book | CAGR | Worst DD | Ret/DD | End $ | Qtrs beat SPY | Fees $ (%/yr) |
|---|---|---|---|---|---|---|
| SPY | 14.3% | -33.7% | 0.43 | 18,843 | | |
| EW universe | 12.0% | -40.7% | 0.29 | 14,094 | | |
| 20 slots ibkr | 4.0% | -51.3% | 0.08 | 5,160 | 35% | 2,379 (3.9%) |
| 20 slots uncon | 9.3% | -50.1% | 0.19 | 10,183 | 43% | 352 (0.4%) |
| 8 slots ibkr | 4.1% | -58.4% | 0.07 | 5,237 | 39% | 1,087 (1.7%) |
| 8 slots uncon | 6.6% | -55.4% | 0.12 | 7,180 | 41% | 307 (0.4%) |
| All signals | 11.9% | -49.9% | 0.24 | 14,001 | 50% | 477 (0.5%) |

A top-5% share above 100% means the best 5% of positions made more than the
whole book; the rest lost money in total.

### Rough 1-year scenarios (full period, overlapping 252-day windows)

Historical frequencies from this backtest, not forecasts.

| Book | Lose money | Typical (median) | Bad year (10th pct) | Good year (90th pct) | Beat SPY |
|---|---|---|---|---|---|
| SPY | 13% | +16% | -3% | +29% | n/a |
| 20 slots ibkr | 46% | +2% | -19% | +31% | 22% |
| 8 slots ibkr | 42% | +5% | -28% | +40% | 34% |
| All signals | 29% | +11% | -17% | +43% | 36% |

## Verdict

**Fails the pre-declared rule.** In the holdout, the best $3,000 IBKR book
(8 slots) made 12.9% a year against SPY 20.1%, the EW universe 15.0% and the
fixed SPY/SHY mix (100% SPY) 20.1%, with a worse return/drawdown (0.43 vs
1.07). The top decile's 60-day abnormal return was negative in development
(-0.4%), so the signal condition fails too.

- **No signal:** top-decile reactions did not drift up afterwards in either
  period, and the decile ladder is flat. Even the cost-free all-signals
  portfolio trailed SPY and the EW universe in both periods.
- **Costs make it worse:** ~4 round trips a year per slot. On $3,000 with 20
  slots ($150 orders), the $1 minimum cost 3.9-6.1% a year; 8 slots cost
  1.7-2.5%.
- **Fixed-weight note:** the matched-mix weight was fixed on development as
  the requester asked. In the PGM run the weight was re-solved per period;
  that made no difference there because every book's drawdown exceeded
  SPY's (w = 1) in every period.

**Bottom line: shelve.** A price-reaction PEAD proxy shows no drift in
liquid US stocks from 2013 to 2026. A true earnings-surprise version would
need point-in-time analyst estimates, which are not available free.

## Files

- `pead_metrics.json` (metrics + decile diagnostic), `pead_curves.parquet`,
  `pead_positions.parquet`, `pead_events.parquet` (every scored event),
  `pead_engine_2026-11-16.log`.
- Cache (gitignored): `backtest/cache/pead_events_raw.parquet`.
