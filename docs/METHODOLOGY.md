# Methodology

This document justifies every number the screener produces. Nothing in the
pipeline is a black box: every metric has a formula, a reason, and a citation
to the research that established it.

## Philosophy

The screener is a **multi-factor model** — the same core approach used by
quantitative asset managers (AQR, Dimensional, Robeco, the quant desks of the
major banks) and documented across five decades of academic finance. The idea:
certain measurable characteristics of stocks have historically been associated
with better subsequent returns, persistently, across markets and time periods.
Rather than betting on any one characteristic, combine several with low
correlation to each other.

Three design principles:

1. **Only well-evidenced factors.** Hundreds of "factors" have been published;
   most fail replication (Harvey, Liu & Zhu 2016). We use only the four
   families with the deepest, most replicated evidence base.
2. **Transparent arithmetic.** Ranks, z-scores and weighted averages — no
   machine-learned black box deciding anything. A human can recompute any
   stock's score by hand.
3. **Honest self-measurement.** The system records its predictions and grades
   itself against them. If the factors stop working, the record will show it —
   that record *is* the experiment.

## The universe

Every US-exchange-listed common stock (NYSE, Nasdaq, NYSE American, etc.) that
files financial statements with the SEC. Excluded, with reasons:

| Exclusion | Why |
|---|---|
| ETFs, closed-end funds, trusts of funds | Not operating businesses; fundamentals don't apply |
| Warrants, rights, units, preferreds, notes | Derivative/credit instruments, not equity claims on a business |
| Price < $2 | Penny-stock territory: unreliable data, enormous spreads, manipulation |
| Median dollar volume < $500k/day | Quotes are noise; a real investor couldn't trade it anyway |
| Market cap < $50M (when known) | EDGAR coverage is thin and data quality poor below this |

These are **tradability filters**, not judgments of quality — a stock excluded
here isn't "bad", it's unmeasurable.

## The four factors

### 1. Value — are you paying a low price for the business?

Stocks priced cheaply relative to their fundamentals have historically outper-
formed expensive ones (Basu 1977 for P/E; Fama & French 1992 established
book-to-market as a core return factor; Fama & French 2015 embed it in the
five-factor model).

| Metric | Formula | Direction |
|---|---|---|
| Earnings yield | net income ÷ market cap | higher is better |
| Free-cash-flow yield | (operating cash flow − capex) ÷ market cap | higher is better |
| Book-to-market | shareholders' equity ÷ market cap | higher is better |
| Net share issuance | Δ shares outstanding over ~1 year | **lower** is better |

We use several value measures rather than one because each can be individually
distorted (buybacks distort book value, one-off charges distort earnings);
averaging them is standard practice (e.g. AQR's value composites — Asness,
Moskowitz & Pedersen 2013, *Value and Momentum Everywhere*).
Negative book value is treated as missing, not as "expensive".

Net issuance sits in the value family because it is a direct claim on the
per-share arithmetic: companies quietly shrinking their share count have
outperformed diluters by a wide, persistent margin (Pontiff & Woodgate 2008),
and heavy issuance is the classic mark of an expensive stock being used as
currency.

### 2. Quality — is it a good business with honest earnings?

Profitable, conservatively financed companies with cash-backed earnings have
outperformed junk, controlling for price (Novy-Marx 2013 for gross
profitability — "the other side of value"; Sloan 1996 for accruals; Piotroski
2000 for fundamental strength scoring; Asness, Frazzini & Pedersen 2019,
*Quality Minus Junk*).

| Metric | Formula | Direction |
|---|---|---|
| Gross profitability | gross profit ÷ total assets | higher is better |
| Return on assets | net income ÷ total assets | higher is better |
| Leverage | total liabilities ÷ total assets | **lower** is better |
| Accruals | (net income − operating cash flow) ÷ total assets | **lower** is better |
| Asset growth | Δ total assets over ~1 year | **lower** is better |
| Insider net buying | insider open-market buys − sells ($, ~2 quarters) ÷ market cap | higher is better |

The accruals metric deserves a note: it flags companies whose reported profits
are *not* showing up as actual cash. Sloan (1996) showed high-accrual firms
systematically disappoint later — it is as close to an "earnings honesty"
meter as public data provides.

Asset growth captures capital discipline: firms that balloon their balance
sheets underperform firms that grow carefully (Cooper, Gulen & Schill 2008 —
the "investment" leg of the Fama-French five-factor model). Insider net
buying uses the SEC's Form 3/4/5 records: officers and directors buying their
own stock in the open market with their own money is one of the oldest
documented positive signals (Seyhun 1986; Lakonishok & Lee 2001), while
routine selling means little — hence a *net dollar* measure where clustered
buys stand out.

### 3. Momentum — is the market already recognising it?

Stocks that performed well over the past 6–12 months have tended to keep
outperforming over the next several months (Jegadeesh & Titman 1993; confirmed
in nearly every market and asset class studied — Asness, Moskowitz & Pedersen
2013). It is the single most robust anomaly in the literature, and also the
one with the sharpest occasional crashes — which is why it is one factor of
four, not the whole model.

| Metric | Formula | Direction |
|---|---|---|
| 12-1 momentum | total return months −12 to −1 | higher is better |
| 6-month momentum | total return months −6 to −1 | higher is better |

The most recent month is **skipped** in both, per the standard construction:
returns over the last few weeks tend to *reverse* (short-term reversal), so
including them degrades the signal.

### 4. Low volatility — does it get there calmly?

Contrary to textbook theory, the least volatile stocks have historically
delivered similar or better returns than the most volatile ones, with far
smaller drawdowns (Ang, Hodrick, Xing & Zhang 2006; Baker, Bradley & Wurgler
2011; related to betting-against-beta, Frazzini & Pedersen 2014). High-vol
lottery-ticket stocks are systematically overpriced.

| Metric | Formula | Direction |
|---|---|---|
| Volatility | annualised std-dev of daily returns, past 12 months | **lower** is better |

## From metrics to scores

1. **Rank each metric within its sector** (metrics where lower is better are
   sign-flipped). Sectors come from SEC SIC codes mapped to ~12 broad groups.
   This matters more than any single metric choice: banks *always* look cheap
   on book value and utilities *always* look low-vol, so global ranking makes
   the composite a disguised sector bet. Within-sector ranking asks the only
   fair question — is this stock cheap, good, trending and calm *versus its
   own peers*? Sectors with fewer than 30 scored stocks fall back to global
   ranking.
2. **Gaussian rank z-score**: map each rank percentile through the inverse
   normal CDF. A z of +1 still reads as "about a standard deviation better
   than average", but the transformation is immune to the violently
   fat-tailed distributions financial ratios always have — a biotech up 400%
   lands near z ≈ +3.4 instead of an unbounded raw z-score that would
   single-handedly dominate the composite. Rank-based scoring is standard
   robust practice in quantitative equity, and it puts every metric on an
   identical score distribution so factors combine on equal footing.
3. **Factor score** = mean of the factor's available metric z-scores.
4. **Composite** = weighted average of the four factor scores, using the
   current weights (renormalised over the factors the stock actually has).
   Stocks missing a factor entirely are still scored, but the dashboard's
   headline list requires complete data on all four.

Starting weights (before any learning): quality 0.30, momentum 0.30,
value 0.25, low-vol 0.15 — proportions in line with published multi-factor
practice, deliberately unexciting.

## Self-improvement

Two mechanisms, both automatic, both fully logged:

**Outcome tracking.** When a scan becomes 21 / 63 / 126 trading days old
(≈1/3/6 months), the system records:

- the realised forward return of every scored stock;
- the mean return of the **top decile** of ranked stocks versus the **median**
  of the whole scan — the headline "did the picks work?" spread;
- a **bootstrap p-value**: the fraction of 2,000 random same-size portfolios
  drawn from the same scan that matched or beat the top decile. p ≈ 0.5 means
  the ranking added nothing; persistently small p is what skill looks like.
  Stocks that delist or vanish from the data are marked at their last
  recorded price rather than silently dropped — otherwise the evaluation
  would delete its own failures (survivorship bias);
- each factor's **information coefficient (IC)**: the Spearman rank
  correlation between the factor's scores on scan day and subsequent returns.
  The IC is the standard signal-quality measure in quantitative asset
  management (Grinold & Kahn, *Active Portfolio Management*). An IC
  persistently above zero means genuine predictive power; ICs of 0.02–0.05
  are respectable in practice.

**Weight adaptation.** Once at least **6 independent (non-overlapping)
evaluation windows** have matured at the 63-day horizon, weights drift toward
each factor's demonstrated mean IC (negative-IC factors get no positive
credit). Independence matters: daily scans with 63-day horizons overlap
almost entirely, so consecutive matured scans are the *same quarter observed
repeatedly* — counting them individually would let the weights chase a single
market regime. Only windows at least 63 trading days apart count toward the
trigger (≈1.5 years before the first adaptation); the IC point estimate then
uses all matured scans for smoothness.

```
target_f  = max(mean_IC_f, 0) / Σ max(mean_IC, 0)
new_f     = 0.75 × current_f + 0.25 × target_f     (then floored at 0.05 and renormalised)
```

Design choices, and why:

- **Slow learning rate (0.25)** — factor performance is noisy over months;
  chasing recent results is the classic quant mistake.
- **Weight floor (0.05)** — no factor is ever fully abandoned. Factors go
  through multi-year droughts and come back (value 2018–2020 being the famous
  recent example); a system that deletes a factor at the bottom of its cycle
  learns exactly the wrong lesson.
- **Every change is written to `weights_history`** with the ICs that caused
  it. The system's learning is auditable forever.

Beyond the automatic loop, the project protocol includes a **periodic written
review** (see `journal/`): examine the track record, the factor ICs, and any
systematic mistakes, and propose changes as documented, justified commits —
never silent edits.

## Known weaknesses (read this)

- **Post-publication decay.** Factors weaken once widely known (McLean &
  Pontiff 2016). Expected edges are modest — a few percent a year of spread,
  not miracles. That is precisely what the track record exists to measure.
- **Cash-flow items are annual.** Income-statement flows (revenue, income)
  are trailing-twelve-month where filings allow, but cash-flow statements are
  filed year-to-date, which the bulk API cannot splice — CFO and capex use
  the latest annual figure and can be up to a year stale.
- **Insider data lags.** The SEC publishes Form 3/4/5 data sets quarterly,
  so the insider metric is one to four months behind; acceptable because the
  signal's evidence is at multi-month horizons, but worth remembering.
- **Sector buckets are coarse.** ~12 groups from SIC ranges; a conglomerate
  gets one label. Good enough for fair ranking, not a real industry model.
- **No transaction-cost or capacity modelling.** This is a screener, not a
  backtested trading strategy.
- **Survivorship-free by construction going forward** — the database keeps
  delisted stocks' scans and outcomes — but there is no pre-launch backtest
  here precisely because backtests of these factors already exist in the
  cited literature; this project measures live performance.

## Changelog

**v0.2 — 2026-09-01** (same day as launch, before any outcomes existed, so
no results were harmed in the making of these changes):
- Sector-neutral scoring (SIC-based, ~12 groups) — removes structural sector
  bets from the composite.
- TTM income-statement fundamentals via quarterly-frame splicing.
- Shares outstanding coalesced across 4 XBRL tags + per-company
  `companyfacts` fallback — fixed missing market caps for multi-class filers
  (Alphabet, Meta and others were previously excluded by this gap).
- New metrics: net share issuance (value), asset growth and insider net
  buying (quality).
- Survivorship guard in outcome evaluation (delisted stocks marked at last
  recorded price, not dropped).
- Market-cap sanity guard: when revenue exceeds 30× or book equity 15× the
  computed market cap, the share count is presumed wrong (single-class tag on
  a multi-class filer — Berkshire is the canonical case) and market cap is
  treated as missing instead of producing an absurd value score.
- Bootstrap p-values on every matured-scan evaluation.

**v0.2.1 — 2026-09-01**: learning-trigger independence fix. The weight
adaptation threshold now counts only non-overlapping 63-day evaluation
windows instead of raw matured-scan count — raised in an external-critique
review: overlapping daily scans are one observation, not many.

**v0.1 — 2026-09-01**: initial release (4 factors, 10 metrics, global
Gaussian-rank scoring).

## References

- Ang, Hodrick, Xing & Zhang (2006), *The Cross-Section of Volatility and Expected Returns*, Journal of Finance.
- Asness, Frazzini & Pedersen (2019), *Quality Minus Junk*, Review of Accounting Studies.
- Asness, Moskowitz & Pedersen (2013), *Value and Momentum Everywhere*, Journal of Finance.
- Baker, Bradley & Wurgler (2011), *Benchmarks as Limits to Arbitrage*, Financial Analysts Journal.
- Basu (1977), *Investment Performance of Common Stocks in Relation to Their Price-Earnings Ratios*, Journal of Finance.
- Carhart (1997), *On Persistence in Mutual Fund Performance*, Journal of Finance.
- Cooper, Gulen & Schill (2008), *Asset Growth and the Cross-Section of Stock Returns*, Journal of Finance.
- Fama & French (1992), *The Cross-Section of Expected Stock Returns*; (2015), *A Five-Factor Asset Pricing Model*.
- Frazzini & Pedersen (2014), *Betting Against Beta*, Journal of Financial Economics.
- Grinold & Kahn (2000), *Active Portfolio Management*, 2nd ed.
- Harvey, Liu & Zhu (2016), *…and the Cross-Section of Expected Returns*, Review of Financial Studies.
- Jegadeesh & Titman (1993), *Returns to Buying Winners and Selling Losers*, Journal of Finance.
- Lakonishok & Lee (2001), *Are Insider Trades Informative?*, Review of Financial Studies.
- McLean & Pontiff (2016), *Does Academic Research Destroy Stock Return Predictability?*, Journal of Finance.
- Novy-Marx (2013), *The Other Side of Value: The Gross Profitability Premium*, Journal of Financial Economics.
- Piotroski (2000), *Value Investing: The Use of Historical Financial Statement Information*, Journal of Accounting Research.
- Pontiff & Woodgate (2008), *Share Issuance and Cross-Sectional Returns*, Journal of Finance.
- Seyhun (1986), *Insiders' Profits, Costs of Trading, and Market Efficiency*, Journal of Financial Economics.
- Sloan (1996), *Do Stock Prices Fully Reflect Information in Accruals and Cash Flows about Future Earnings?*, The Accounting Review.
