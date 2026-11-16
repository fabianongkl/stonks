# Profitable Growth + Momentum (PGM) screen: frozen rules

**Shelved 2026-11-16 (user decision).** Not a strategy: held-out gain came
with about twice SPY's drawdown, t < 0.5, mostly from APP. Kept only as a
watchlist ranking. See `backtest/pgm/results/RUN_LOG_2026-11-16.md`.

Status: **rules frozen 2026-11-16, before any data was downloaded or any
backtest was run.** Nothing below may be changed after seeing results. If a
rule turns out to be broken (a bug, not a disappointing number), the fix is
logged in the run log with the reason, and the held-out period is reported
both before and after the fix.

This is a research backtest inside Open Screener. It never sets live weights,
never touches the paper books, and places no orders.

## 1. Question

Can a point-in-time screen for *profitable, accelerating, already-rising*
companies (the profile that Nvidia, Micron or Palantir showed before their big
runs) beat simple alternatives after realistic costs on a $3,000 IBKR cash
book? Bessembinder (2018) found ~4% of US stocks produced all of the market's
net gain, so most of any edge should come from a handful of positions. The
test reports that concentration directly.

## 2. Data (point-in-time)

- **Fundamentals:** SEC EDGAR XBRL `companyfacts`, one request per company,
  under SEC fair-access rules (<= 8 requests/s, generic `OpenScreener`
  User-Agent with no personal details).
- **Availability rule:** every fact carries its EDGAR `filed` date. For each
  (company, field, period end, period length) only the **first filed** value
  is kept (the as-reported number; later restatements are ignored). A derived
  number (e.g. a quarter computed as 6-month YTD minus 3-month YTD) becomes
  known on the latest `filed` date of its inputs. At rebalance date `t` the
  screen may use only numbers known on or before `t - 1 day`.
- **Prices:** Yahoo daily bars, `Close` (split-adjusted) and `Adj Close`
  (split + dividend adjusted), plus the split history so true historical
  (unadjusted) prices can be rebuilt for whole-share sizing and the $5 filter.
- **Universe source:** every company in the screener's current universe
  (exchange-listed US common stocks that file with the SEC).

### Survivorship bias that remains

Free data has no price history for most delisted tickers, so companies that
went bankrupt, were acquired or were delisted between 2013 and 2026 are
**missing**. The run log measures how many 2013 EDGAR revenue filers are not
in the universe. The bias inflates absolute returns of everything tested here,
including the equal-weight universe benchmark. Because the strategy and that
benchmark share the same bias, **the strategy vs equal-weight-universe gap is
the most trustworthy comparison**; the gap vs SPY is not, since SPY's history
is complete.

## 3. Timeline and holdout

- Prices from 2011-06-01 (momentum and 52-week-high warm-up).
- **Quarterly rebalances** on the last trading day of **Feb, May, Aug, Nov**
  (after most 10-Q/10-K filings for the prior quarter are public). Trades fill
  at that day's close.
- First rebalance 2013-02-28. Last rebalance 2026-08-31, held to the last
  available close (mid-Nov 2026).
- **Holdout = last ~25% of the period: rebalances from 2023-05-31 onward.**
  Development = 2013-02-28 to 2023-05-31. All rules are fixed before either
  is run; no parameter is chosen by looking at either period. Both are
  reported separately, plus the full period.

## 4. Investable universe (at each rebalance `t`)

1. Has a price on `t` and at least 252 trading days of history.
2. Unadjusted price >= $5.
3. Median daily dollar volume over the prior 63 days >= $1M.
4. Market cap >= $300M (unadjusted price x latest known shares outstanding).
5. Not a financial company (SIC 6000-6799): gross profit / assets is
   meaningless for banks and insurers (Novy-Marx 2013 excludes them too).

This universe is also the **equal-weight universe benchmark**.

## 5. Eligibility gates ("profitable")

- Trailing-twelve-month (TTM) operating cash flow > 0.
- TTM revenue > 0.
- Latest fundamental period end no older than 200 days at `t` (stale filers
  are skipped).
- 12-1 month momentum must be computable.

## 6. Signals and weights

Each signal is turned into a percentile rank (0 to 1) across the eligible
stocks at `t`. A missing signal gets 0.5 (neutral). Composite = weighted sum.

| Family | Signal | Definition | Weight |
|---|---|---|---|
| Profitability | Gross profit / assets | TTM gross profit / latest total assets. Gross profit = `GrossProfit` tag, else revenue minus `CostOfRevenue` / `CostOfGoodsAndServicesSold` | 12.5% |
| Profitability | Rising operating cash flow | (TTM OCF now - TTM OCF four quarters earlier) / latest total assets | 12.5% |
| Growth | Revenue growth | TTM revenue / TTM revenue four quarters earlier - 1 | 10% |
| Growth | Growth acceleration | revenue growth now - revenue growth one quarter earlier (both TTM YoY) | 7.5% |
| Growth | Margin expansion | TTM operating margin now - four quarters earlier (gross margin if operating income is missing) | 7.5% |
| Surprise | SUE (proxy) | (latest quarter operating income - same quarter last year) / std dev of that YoY change over the prior 8 quarters (needs >= 4) | 10% |
| Momentum | 12-1 month return | total return from t-252 to t-21 trading days | 20% |
| Momentum | Near 52-week high | price / max price over prior 252 days | 10% |
| Issuance | Low net issuance | -(weighted-average basic shares, latest quarter / same quarter a year earlier - 1), both values taken from the **same filing** so stock splits cancel | 10% |

Total 100%. Weights were set a priori (roughly one-third profitability +
growth quality, one-third momentum, the rest surprise and issuance) and are
not tuned.

**Earnings surprise / revisions:** there is no free point-in-time source of
analyst consensus or revisions, so true surprise vs consensus is not tested.
SUE vs a seasonal random walk (Foster, Olsen and Shevlin 1984) is the free
substitute.

## 7. Portfolios

Two sizes, each in two cost regimes (four books):

- **Broad:** top 25 names. **Concentrated:** top 8 names.
- **Hold buffer:** an existing holding is kept while its rank is within 2x the
  book size (top 50 / top 16); exits are sold, and the book is filled back to
  N from the top of the ranking. Holdings that leave the investable universe
  or eligibility gates are sold.
- If a stock's price series ends between rebalances, it is treated as sold at
  its last close (the remaining value sits in cash).

### $3,000 IBKR cash book

- Start $3,000 cash, no margin, no shorting, no cash yield (conservative).
- Costs per order: commission max($1, $0.005 x shares) plus $0.01/share
  slippage. Whole shares only.
- Each new buy targets (book value / N). Shares = floor(target / price). If
  that is 0, buy 1 share only if price <= 1.5x target and cash allows;
  otherwise skip the name and take the next ranked one.
- Continuing holdings are **not** rebalanced back to equal weight (that would
  pay $1 minimums to move a few dollars). Sells happen before buys.
- Positions grow with total return (dividends treated as reinvested).

**Fractional shares note:** IBKR offers fractional shares, which removes the
whole-share skip problem, but the fixed plan still charges per order
(IBKR caps fixed commissions at 1% of trade value, so tiny fractional orders
cost under $1). Fractional sizing on $3,000 is close to the unconstrained
book below minus roughly $1 per order.

### Unconstrained book

- Fractional shares, rebalanced exactly to equal weight each quarter.
- Cost 5 bps of traded value per side (no minimums). Positions grow with total
  return.

## 8. Benchmarks

1. **SPY buy-and-hold** (total return).
2. **Equal-weight universe:** every stock in section 4, equal weighted,
   rebalanced each quarter, no costs.
3. **Static SPY/SHY mix with the same worst drawdown** as each strategy book:
   monthly-rebalanced w x SPY + (1-w) x SHY, with w solved so the mix's worst
   drawdown over the same period equals the book's. If the book's drawdown is
   deeper than SPY's, w = 1 (plain SPY) and that is stated.

## 9. Metrics

For each book and each period (development, holdout, full):

- Annual return (CAGR) and worst drawdown (on daily values).
- Return / drawdown (CAGR / |max drawdown|).
- Hit rate: share of closed positions with a positive return; also share of
  quarters that beat SPY.
- Concentration: share of the book's total dollar P&L from the top 1% and the
  top 5% of positions (ranked by dollar P&L).
- Turnover: average annual one-sided traded value / average book value.
- Fees: total commissions + slippage in dollars and as % of average book
  value per year.

## 10. Verdict rule (declared in advance)

The screen "works" for the $3,000 book only if, **in the holdout**, the IBKR
book beats both (a) the equal-weight universe and (b) the drawdown-matched
SPY/SHY mix on CAGR. Beating SPY alone is not enough (the prior strategy work
showed that SPY/SHY matched for drawdown is the real bar). Development-period
results are context only.

## Results

Appended after the run; the rules above are unchanged. First run:
[backtest/pgm/results/RUN_LOG_2026-11-16.md](../backtest/pgm/results/RUN_LOG_2026-11-16.md).
