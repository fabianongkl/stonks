# Methodology change record — v0.2 (2026-09-01)

Same-day upgrade, made *before any outcomes existed*, so no results were
peeked at in making these decisions. Trigger: a user question ("why no AI
stocks?") led to an audit of how large-cap names scored, which exposed a data
gap and two structural weaknesses.

## What the audit found

- **GOOGL scored +0.62 — top-30 territory — but was excluded from headline
  picks** because its shares outstanding never appear under the `dei` XBRL
  tag the screener relied on. META, TSM, AVGO, PLTR were excluded the same
  way. Data gap, not model opinion.
- The day-one top 10 held two energy names, two insurers and two hotel
  REITs — evidence the composite was partly a sector bet (value and low-vol
  structurally favour financials/REITs/energy).
- Outcome evaluation silently dropped stocks that vanish from the data —
  which would have flattered the track record by deleting delistings.

## Changes shipped (details in METHODOLOGY.md changelog)

1. Shares outstanding: 4-tag coalesce + per-company companyfacts fallback.
2. Sector-neutral scoring (SIC → ~12 sectors; rank within sector).
3. TTM income-statement fundamentals via quarterly-frame splicing.
4. Survivorship guard: vanished stocks marked at last recorded price.
5. New metrics: net share issuance (value), asset growth + insider net
   buying (quality).
6. Bootstrap p-value per matured-scan evaluation.

## Portfolio impact

Holdings unchanged (grandfathered). Amendment: future selections respect a
max-2-per-sector cap. The scan of 2026-09-01 was re-run under v0.2, so the
recorded ranks for this date reflect the new methodology; the portfolio's
entry prices and positions are untouched.
