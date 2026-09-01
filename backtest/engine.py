"""Monthly-rebalance backtest engine.

Reuses the LIVE screener's scoring code (screener.factors) verbatim — that is
the point: this tests the actual instrument, not a reimplementation.  Fixed
v0.2 default weights throughout (no learning, no tuning to the past).

Simulated each month over the window:
  * decile portfolios (the science): mean forward 1-month return of each
    composite decile among full-coverage stocks, vs the scored median;
  * factor information coefficients;
  * a stateful "Claude's Picks" book under the live portfolio rules
    (top 10, sector cap 2, sell past rank 50, IBKR fees);
  * benchmark: SPY.

Known biases (quantified in the report, do not skip reading it):
  * survivorship — the universe is TODAY'S listings; companies delisted
    during the window are absent, which flatters results;
  * stocks that stop trading mid-window are carried at last price (0% from
    their last print) rather than vanishing.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener import config, factors, portfolio, universe
from screener.data import sectors
from . import config_bt, pit

log = logging.getLogger("bt.engine")

FUND_FIELDS = ["revenue", "net_income", "op_income", "gross_profit",
               "cfo", "capex", "assets", "liabilities", "equity", "cash", "shares"]


# ---------------------------------------------------------------------------
# Vectorised price metrics on the (dates x symbols) close/volume matrices
# ---------------------------------------------------------------------------

def price_metrics_at(C: pd.DataFrame, V: pd.DataFrame, idx: int) -> pd.DataFrame:
    lo = max(0, idx - 430)
    sub = C.iloc[lo:idx + 1]
    last = sub.iloc[-1]

    def total_ret(lb: int, skip: int) -> pd.Series:
        if sub.shape[0] < lb + skip + 1:
            return pd.Series(np.nan, index=C.columns)
        a, b = sub.iloc[-(lb + skip + 1)], sub.iloc[-(skip + 1)]
        return (b / a - 1).where(a > 0)

    rets = np.log(sub.where(sub > 0)).diff().iloc[-252:]
    nobs = rets.notna().sum()
    vol = (rets.std(ddof=1) * np.sqrt(252)).where(nobs >= 60)

    dvol = (C.iloc[max(0, idx - 62):idx + 1] * V.iloc[max(0, idx - 62):idx + 1]).median()

    return pd.DataFrame({
        "last_close": last,
        "mom_12_1": total_ret(252, 21),
        "mom_6": total_ret(126, 21),
        "volatility": vol,
        "dollar_volume": dvol,
    })


# ---------------------------------------------------------------------------
# Stateful Claude's-Picks-rules book
# ---------------------------------------------------------------------------

class SimBook:
    def __init__(self, start_cash: float = 10_000.0):
        self.cash = start_cash
        self.pos: dict[str, float] = {}
        self.fees = 0.0
        self.trades = 0

    def _fee(self, shares: float, price: float) -> float:
        f = portfolio.ibkr_fee(shares, price)
        self.fees += f
        self.trades += 1
        return f

    def value(self, px: pd.Series) -> float:
        return self.cash + sum(sh * px.get(s, 0.0) for s, sh in self.pos.items())

    def rebalance(self, ranked: pd.DataFrame, px: pd.Series) -> None:
        """ranked: full-coverage stocks with fc_rank, sector, close."""
        rank_of = ranked["fc_rank"]
        # sells: rank decayed past the live rule's threshold, or vanished
        for sym in list(self.pos):
            r = rank_of.get(sym)
            if r is None or np.isnan(r) or r > portfolio.MAX_HELD_RANK:
                p = px.get(sym)
                if p and p > 0:
                    sh = self.pos.pop(sym)
                    self.cash += sh * p - self._fee(sh, p)
                else:
                    self.pos.pop(sym)   # untradable — worthless exit
        # buys: fill to 10 with best-ranked non-held, sector cap 2
        counts: dict[str, int] = {}
        for sym in self.pos:
            sec = ranked["sector"].get(sym, "Other") or "Other"
            counts[sec] = counts.get(sec, 0) + 1
        slots = portfolio.N_POSITIONS - len(self.pos)
        if slots <= 0:
            return
        budget = self.cash / slots
        for sym, row in ranked.sort_values("fc_rank").iterrows():
            if slots == 0 or sym in self.pos:
                continue
            sec = row["sector"] or "Other"
            if counts.get(sec, 0) >= portfolio.MAX_PER_SECTOR:
                continue
            p = row["close"]
            sh = int(min(budget, self.cash) // p)
            if sh <= 0:
                continue
            cost = sh * p + self._fee(sh, p)
            if cost > self.cash:
                continue
            self.cash -= cost
            self.pos[sym] = sh
            counts[sec] = counts.get(sec, 0) + 1
            slots -= 1


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> pd.DataFrame:
    log.info("Loading universe, sectors, prices, PIT facts ...")
    uni = universe.build_universe()
    sec = sectors.get_sectors(uni["cik"].tolist())
    uni = uni.merge(sec[["cik", "sector"]], on="cik", how="left")
    uni["sector"] = uni["sector"].fillna("Other")

    px_long = pd.read_parquet(config_bt.CACHE / "prices_hist.parquet")
    px_long["date"] = pd.to_datetime(px_long["date"])
    C = px_long.pivot_table(index="date", columns="symbol", values="close")
    V = px_long.pivot_table(index="date", columns="symbol", values="volume")
    Cf = C.ffill()
    dates = C.index

    facts = pd.read_parquet(config_bt.CACHE / "pit_facts.parquet")
    timelines = pit.build_timelines(facts)

    # first trading day of each month in the window
    month_starts = pd.date_range(config_bt.FIRST_REBALANCE, config_bt.LAST_REBALANCE,
                                 freq="MS")
    rebs = []
    for m in month_starts:
        pos = dates.searchsorted(m)
        if pos < len(dates):
            rebs.append(dates[pos])
    rebs = sorted(set(rebs))
    log.info("%d monthly rebalances: %s .. %s", len(rebs),
             rebs[0].date(), rebs[-1].date())

    book = SimBook()
    rows = []
    for i, D in enumerate(rebs[:-1]):
        nxt = rebs[i + 1]
        idx = dates.get_loc(D)
        pm = price_metrics_at(C, V, idx)
        df = uni.merge(pm, left_on="symbol", right_index=True, how="inner")
        df = df.dropna(subset=["last_close"])
        df = (df.sort_values("dollar_volume", ascending=False)
                .drop_duplicates("cik").reset_index(drop=True))

        for f in FUND_FIELDS:
            av = pit.asof(timelines, f, df["cik"], D)
            if f == "shares":
                df["shares_out"], df["shares_prior"] = av["val"], av["prior"]
            elif f == "revenue":
                df["revenue"], df["revenue_prior"] = av["val"], av["prior"]
            elif f == "assets":
                df["assets"], df["assets_prior"] = av["val"], av["prior"]
            else:
                df[f] = av["val"]

        df = df[df["last_close"] >= config.MIN_PRICE]
        df = df[df["dollar_volume"].fillna(0) >= config.MIN_DOLLAR_VOLUME]
        df["market_cap"] = df["last_close"] * df["shares_out"]
        df = df[~(df["market_cap"] < config.MIN_MARKET_CAP)]

        df = factors.fundamental_metrics(df)
        scored = factors.score(df, config.DEFAULT_WEIGHTS)
        scored = scored.rename(columns={"last_close": "close"})

        fwd_all = (Cf.loc[nxt] / Cf.loc[D] - 1)
        scored["fwd"] = scored["symbol"].map(fwd_all)
        scored = scored.dropna(subset=["fwd"])

        full = scored[scored["n_factors"] == 4].copy()
        full = full.sort_values("composite", ascending=False).reset_index(drop=True)
        full["fc_rank"] = full.index + 1
        n = len(full)
        if n < 100:
            log.warning("%s: only %d full-coverage stocks — skipping month", D.date(), n)
            continue
        full["decile"] = (full.index * 10 // n) + 1   # 1 = best composite

        dec = full.groupby("decile")["fwd"].mean()
        ics = {f: float(scored[f"{f}_score"].corr(scored["fwd"], method="spearman"))
               for f in factors.FACTOR_NAMES}

        ranked = full.set_index("symbol")[["fc_rank", "sector", "close"]]
        book.rebalance(ranked, Cf.loc[D])
        book_val = book.value(Cf.loc[nxt])

        rows.append({
            "date": D, "next": nxt, "n_scored": len(scored), "n_full": n,
            "top_decile": float(dec.get(1, np.nan)),
            "bottom_decile": float(dec.get(10, np.nan)),
            "median_fwd": float(scored["fwd"].median()),
            "spread": float(dec.get(1, np.nan) - scored["fwd"].median()),
            "spy_fwd": float(fwd_all.get(config_bt.BENCH, np.nan)),
            "book_value": book_val,
            **{f"ic_{k}": v for k, v in ics.items()},
        })
        log.info("%s  top %+0.2f%%  med %+0.2f%%  spread %+0.2f%%  spy %+0.2f%%  book $%.0f",
                 D.date(), dec.get(1, np.nan) * 100, scored['fwd'].median() * 100,
                 (dec.get(1, np.nan) - scored['fwd'].median()) * 100,
                 fwd_all.get(config_bt.BENCH, np.nan) * 100, book_val)

    res = pd.DataFrame(rows)
    res.to_parquet(config_bt.RESULTS / "monthly_results.parquet", index=False)
    log.info("Backtest complete: %d months. Fees paid in sim book: $%.2f over %d trades",
             len(res), book.fees, book.trades)
    return res
