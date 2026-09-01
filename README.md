# Open Screener

A free, open-source, **self-improving daily stock screener** for the whole US
market (~5,300 listed common stocks). It ranks every stock on four factor
families with decades of published academic evidence behind them, explains every
ranking in plain English, keeps a permanent record of every scan — and then
honestly measures whether its own picks actually worked, adapting its factor
weights based on realised results.

**This is a research instrument, not investment advice.** Its stated purpose is
to find out, transparently and over time, whether a systematic factor model
(with an AI maintaining and reviewing it) can identify stocks that go on to
outperform — and to learn from its wins and mistakes in public.

## What it does

Every time you run a scan it:

1. **Builds the universe** — every US-exchange-listed common stock that files
   financial statements with the SEC (ETFs, funds, warrants, preferreds and
   test issues excluded).
2. **Pulls fundamentals** from SEC EDGAR — audited filing data for the whole
   market in ~35 bulk requests (free, official, no API key).
3. **Pulls prices** — ~14 months of daily closes for the whole universe
   (Yahoo Finance end-of-day data).
4. **Scores every stock** on Value, Quality, Momentum and Low-Volatility
   (see [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for the full academic
   justification of every metric).
5. **Records everything** in a local SQLite database, forever.
6. **Evaluates past scans** — once a scan is 1 / 3 / 6 months old, it records
   how every stock actually performed, whether the top-ranked decile beat the
   market median, and how predictive each factor really was.
7. **Adapts its weights** — after enough scans mature, factor weights drift
   toward the factors that demonstrated real predictive power (details in
   [docs/METHODOLOGY.md](docs/METHODOLOGY.md#self-improvement)). Every change
   is logged with its reasoning.
8. **Publishes a dashboard** — `dashboard/index.html`, a self-contained page
   with today's rankings, plain-English explanations, honest warnings, and the
   full track record. Plus a markdown journal entry per scan in `journal/`.

## The portfolio experiment

The rankings are held accountable by **Claude's Picks** — a hypothetical
$10,000 portfolio deployed into the top 10 ranked stocks, tracked daily with
IBKR-style commissions against buy-and-hold SPY
(`dashboard/portfolio.html`). Rules, rationale and honest expectations:
[docs/PORTFOLIO_EXPERIMENT.md](docs/PORTFOLIO_EXPERIMENT.md). Run
`python track_portfolio.py` after each scan.

## Hosted mode (GitHub Actions + Pages)

The repo ships with `.github/workflows/daily-scan.yml`: every weekday after
US market close it scans the market, tracks the portfolio, **commits the
database, journal and dashboard back to the repo** (the whole prediction
record becomes tamper-evident git history), and publishes the dashboard to
GitHub Pages. One-time setup after forking/cloning:

1. **Settings → Pages** → Source: *GitHub Actions*.
2. **Settings → Secrets and variables → Actions** → new secret
   `SEC_CONTACT` = your email (SEC fair-use identification; never shown
   publicly).
3. Run the workflow once manually (Actions → daily-scan → *Run workflow*).

Note: Yahoo occasionally rate-limits cloud runner IPs harder than home
connections; if price downloads fail in Actions, see docs/DATA_SOURCES.md
for the Stooq swap.

## Quick start (local)

```
pip install -r requirements.txt
python run_scan.py
```

First run takes ~10–15 minutes (full-market price download); repeat runs on the
same day are instant thanks to caching. Then open `dashboard/index.html` in any
browser.

Useful flags:

| Flag | Effect |
|------|--------|
| `--sample 200` | quick test scan on a random 200-stock subsample |
| `--no-cache` | force re-download of universe/price/fundamental data |
| `--skip-dashboard` | scan and record only |

Before making the project public, put your own contact address in
`SEC_USER_AGENT` in `screener/config.py` — the SEC's fair-use policy asks every
automated client to identify itself with a real contact.

## Project layout

```
run_scan.py            entry point — one full daily scan
screener/
  config.py            every tunable parameter, documented
  universe.py          which stocks are scannable, and why
  data/fundamentals.py SEC EDGAR bulk fundamentals
  data/prices.py       Yahoo Finance bulk prices
  factors.py           metric + factor score computation
  db.py                SQLite schema and persistence
  learning.py          outcome tracking and weight adaptation
  report.py            HTML dashboard + markdown journal
docs/
  METHODOLOGY.md       what every metric means and the research behind it
  DATA_SOURCES.md      where data comes from, and its honest limitations
data/screener.db       the permanent record (created on first run)
dashboard/index.html   the human-friendly output (regenerated every scan)
journal/               one markdown entry per scan day
```

## Honest limitations

- Income-statement fundamentals are trailing-twelve-month where filings
  allow; **cash-flow items fall back to the latest annual filing** (they're
  filed year-to-date, which the bulk API can't splice).
- ~15–25% of small stocks still lack complete fundamental data even after
  the per-company fallback; they are scored on the factors available but
  excluded from headline picks.
- Yahoo end-of-day prices are the standard free source but are unofficial and
  occasionally gappy for tiny stocks.
- Factor investing works **on average, over years, across many stocks**. Any
  single pick can and will go badly wrong; the tool's warnings (⚠) exist for a
  reason. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## License

MIT — see [LICENSE](LICENSE).
