# Post-earnings announcement drift (PEAD): frozen rules

**Shelved 2026-11-16 (user decision).** No drift found; best held-out $3k
book 12.9%/yr vs SPY 20.1%. See
`backtest/pgm/results/PEAD_RUN_LOG_2026-11-16.md`.

Status: **rules frozen 2026-11-16, before any event data was downloaded or any
PEAD backtest was run.** This is a separate test from the PGM screen
(docs/PGM_SCREEN.md), whose rules and results are not changed by it. Research
only: no live weights, no paper books, no orders.

## 1. Question

Stocks that react strongly to an earnings release tend to keep drifting the
same way for weeks (Ball and Brown 1968; Bernard and Thomas 1989). Free data
has no analyst consensus, so the market's own reaction is the surprise proxy
(as in Chan, Jegadeesh and Lakonishok 1996 and Brandt et al. 2008). Can
buying the strongest reactions beat simple alternatives after realistic costs
on a $3,000 IBKR cash book?

## 2. Events (point-in-time)

- Source: SEC EDGAR `submissions` API (one request per company plus older
  history files back to 2012), same fair-access limits and generic User-Agent
  as the PGM fetch.
- **Earnings event = an 8-K whose items include 2.02** ("Results of
  Operations and Financial Condition"). 8-K/A amendments are ignored.
- **Fallback:** a 10-Q or 10-K filing counts as an event only if the company
  filed no Item 2.02 8-K in the 60 calendar days before it (some firms
  release results only in the periodic report).
- Two events of the same company less than 30 calendar days apart: only the
  first counts.
- **Day 0** = the filing date if EDGAR's acceptance time is before 16:00 New
  York time, otherwise the next trading day. (Acceptance times are UTC and
  are converted.) If day 0 is not a trading day, it moves to the next one.

## 3. Signal

- **Abnormal reaction** = stock total return from the close of day -2 to the
  close of day +1, minus SPY's total return over the same window (the three
  sessions -1, 0, +1).
- **Top decile** = abnormal reaction >= the 90th percentile of all universe
  events whose day +1 fell in the prior 63 trading days (needs >= 200 such
  events; otherwise no trade). The cut-off uses only past events.
- **Entry at the close of day +2** (a market-on-close order placed after the
  day +1 close is known). The idea's "next open after day +2" is not
  available because the price cache has closes only; entering at the day +2
  close uses the same information one session earlier.
- **Exit at the close 60 trading days after entry.** No stops, no early exit.
- Universe filter at day 0, same as PGM section 4: price history >= 252 days,
  unadjusted price >= $5, 63-day median dollar volume >= $1M, market cap >=
  $300M (shares known by day 0 - 1), non-financial (SIC outside 6000-6799).

## 4. Timeline and holdout

- Events from 2013-03-01. Prices to 2026-11-13.
- **Holdout: entries from 2023-05-31 onward** (same cut as PGM, ~last 25%).
  Development: entries 2013-03-01 to 2023-05-30.
- Each period is simulated separately from $3,000; a full-period run is also
  reported. Positions open at a period's end are marked at its last close.

## 5. Books

Signals arrive daily, so books use slots:

- **Slot books: 20 slots and 8 slots.** Each trading day, free slots are
  filled with that day's entry signals, strongest reaction first. Signals
  with no free slot are skipped (never queued). A stock already held is not
  bought again.
- **$3,000 IBKR book:** slot size = current book value / slots. Same costs and
  whole-share rules as PGM: commission max($1, $0.005/share) + $0.01/share
  slippage per order; shares = floor(slot / price); if 0, buy 1 share only
  if price <= 1.5x slot and cash allows, else skip. No buy if cash < half a
  slot. Cash earns nothing. Positions grow with total return.
- **Unconstrained slot book:** fractional shares, 5 bps per side.
- **All-signals portfolio (signal test, no capital limit):** equal weight
  across every active top-decile position, re-weighted daily, 5 bps per side
  on each entry and exit; cash (0%) on days with no positions.

Fractional shares note: IBKR's fixed plan caps commission at 1% of trade
value, so fractional orders under $100 pay under $1.

## 6. Benchmarks

1. SPY buy-and-hold.
2. Equal-weight PGM universe (same filters, rebalanced quarterly).
3. **Static SPY/SHY mix with the same worst drawdown, weight fixed on the
   development period** and then applied unchanged to the holdout and full
   period (never re-solved on held-out data). If a book's development
   drawdown exceeds SPY's, w = 1.

## 7. Metrics

Same as PGM: CAGR, worst drawdown, return/drawdown, hit rate (positions with
positive P&L), share of quarters beating SPY, top 1% / 5% of positions' share
of P&L, turnover, fees. Plus a diagnostic, not a strategy: average 60-day
abnormal return (vs SPY) by reaction decile, development vs holdout.

## 8. Verdict rule (declared in advance)

PEAD "works" for the $3,000 book only if, in the holdout, the IBKR slot book
beats SPY, the equal-weight universe and the development-fixed SPY/SHY mix on
CAGR **and** has a better return/drawdown than SPY. (The second condition was
added because the PGM run showed CAGR alone lets a riskier book pass.) It also
needs the top decile's 60-day abnormal return to be positive in both periods.

## Results

Appended after the run; the rules above are unchanged. First run:
[backtest/pgm/results/PEAD_RUN_LOG_2026-11-16.md](../backtest/pgm/results/PEAD_RUN_LOG_2026-11-16.md).
