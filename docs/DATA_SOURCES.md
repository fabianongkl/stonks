# Data sources

Every data source is free and requires no API key, so anyone can clone and run
the project. Each source's honest limitations are listed — data quality is the
first place a screener silently goes wrong.

## 1. SEC EDGAR (fundamentals) — primary source

**What:** Audited financial-statement data (XBRL) for every company that files
with the US Securities and Exchange Commission.
**How:** The [XBRL frames API](https://www.sec.gov/edgar/sec-api-documentation)
returns one accounting concept for *every filer* per request, so a full-market
refresh costs ~35 small requests instead of 5,000 per-company calls:

```
https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2026Q2I.json
```

**Why it's the right source:** it is the *primary* record — the actual audited
filings that commercial data vendors repackage and sell. Zero cost, zero
survivorship bias, official.

**Etiquette:** the SEC asks automated clients to identify themselves.
`SEC_USER_AGENT` in `screener/config.py` must be of the form
`"ProjectName contact@example.com"` — put a real contact there. Requests are
rate-limited in code to stay under the SEC's 10-requests/second fair-use limit.

**Two layers:** the bulk frames feed the whole market; companies the frames
miss (odd fiscal years, multi-class share structures such as Alphabet, whose
shares outstanding never appear under the common `dei` tag) get a targeted
per-company `companyfacts` fetch, cached 30 days and capped per run.

**Limitations:**

- Income-statement flows are trailing-twelve-month where quarterly filings
  allow (sum of four quarters, else annual + post-year-end quarters − the
  same quarters a year earlier). **Cash-flow items are filed year-to-date**,
  which quarterly frames cannot see, so CFO/capex use the latest annual
  figure.
- Tag inconsistency: companies report revenue under several different XBRL
  tags; we coalesce across the common ones (and shares outstanding across
  four tags), but exotic filers still slip through.
- Coverage in practice: ~75–85% of the tradable universe ends up with
  complete data after the fallback layer; the rest are scored on what exists
  and excluded from headline picks.

**Also from EDGAR:**

- **Sectors** — each filer's SIC code from the company browse endpoint
  (fetched once per company, cached forever), mapped to ~12 broad sectors
  for within-sector ranking.
- **Insider transactions** — the SEC's quarterly Form 3/4/5 structured data
  sets (bulk TSV archives). Net open-market buys minus sells per issuer over
  the latest two available quarters. Publication lags up to a quarter; if a
  quarter's archive isn't available the metric degrades gracefully.

## 2. Yahoo Finance via `yfinance` (prices)

**What:** ~14 months of split/dividend-adjusted daily closes and volumes for
the whole universe, downloaded in batches.

**Why:** it is the de-facto standard free price source in open-source finance.
No key, whole-market coverage, adjusted prices.

**Limitations — read honestly:**

- It is an **unofficial** library reading Yahoo's endpoints; Yahoo can change
  or restrict them at any time. If that happens, `screener/data/prices.py` is
  the single file to swap (Stooq bulk EOD is the natural fallback and the
  module is written to make that swap contained).
- End-of-day data only — this project is a daily screener, not a live feed.
- Tiny stocks occasionally have gaps or bad prints; the winsorisation step and
  the tradability filters exist partly for this.

## 3. Nasdaq Trader symbol directory (universe)

Official daily symbol files for all US exchanges
(`nasdaqlisted.txt`, `otherlisted.txt`), including ETF and test-issue flags.
Free, no key. Used with SEC's `company_tickers.json` to map tickers to CIK
numbers (only stocks that actually file with the SEC are scannable).

## 4. Interactive Brokers (optional enrichment — not required)

The screener never *requires* a brokerage connection. For users who have one,
IBKR is useful **downstream** of the screen: pulling fresh quotes or placing
paper trades on the handful of top-ranked stocks. It is unsuitable as the
screener's backbone (per-request quote model, personal authentication, no bulk
fundamentals) — that's why it is an enrichment layer, not a dependency.

## Caching

| Data | Cache key | Rationale |
|---|---|---|
| Universe | calendar day | listings change daily at most |
| Prices | calendar day | EOD data; re-runs same day are free |
| Fundamentals | ISO week | filings arrive quarterly; weekly refresh is plenty |

Caches live in `data/cache/` and can be deleted at any time (`--no-cache`
forces a refresh).
