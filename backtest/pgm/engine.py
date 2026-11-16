"""PGM backtest engine: rankings, books, benchmarks, metrics.

    python -m backtest.pgm.engine

Rules: docs/PGM_SCREEN.md. Writes backtest/pgm/results/{rankings,metrics,
curves,positions}.parquet and metrics.json for run_log.py.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
import pandas as pd

from . import config as C
from . import signals as S

log = logging.getLogger("pgm.engine")
DAY = pd.Timedelta(days=1)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
class Data:
    def __init__(self):
        facts = pd.read_parquet(C.FACTS)
        prices = pd.read_parquet(C.PRICES)
        uni = pd.read_parquet(C.UNIVERSE)
        log.info("building fundamental panel ...")
        self.fund = S.fundamental_panel(facts)
        self.shares = S.shares_table(facts)
        log.info("building price panels ...")
        pp = S.price_panels(prices)
        self.adj, self.close, self.raw, self.dvol = (pp[k].astype("float64") for k in
                                                     ("adj", "close", "raw", "dvol"))
        self.dates = self.adj.index
        self.adj_ff = self.adj.ffill()
        self.raw_ff = self.raw.ffill()
        self.last_valid = self.adj.apply(lambda s: s.last_valid_index())
        self.first_valid = self.adj.apply(lambda s: s.first_valid_index())

        sic_path = C.ROOT.parents[1] / "data" / "cache" / "sic_map.parquet"
        sic = pd.read_parquet(sic_path).set_index("cik")["sic"]
        uni = uni[uni["symbol"].isin(self.adj.columns)].copy()
        uni["cik"] = uni["cik"].astype(int)
        # one symbol per company: the share class with the most dollar volume
        tot = self.dvol.sum()
        uni["dv"] = uni["symbol"].map(tot)
        uni = uni.sort_values("dv", ascending=False).drop_duplicates("cik")
        uni["sic"] = uni["cik"].map(sic)
        fin = uni["sic"].between(*C.FIN_SIC)
        self.uni = uni[~fin][["symbol", "cik", "sic"]].reset_index(drop=True)
        self.sym2cik = dict(zip(self.uni["symbol"], self.uni["cik"]))
        self.n_fin_excluded = int(fin.sum())

    def rebal_dates(self) -> list[pd.Timestamp]:
        d = pd.Series(self.dates, index=self.dates)
        me = d.groupby([d.index.year, d.index.month]).max()
        out = [x for x in me if x.month in C.REBAL_MONTHS
               and x >= pd.Timestamp(C.FIRST_REBAL)]
        # drop the last month-end if it is not a completed month
        return [x for x in out if x < self.dates[-1]]


# --------------------------------------------------------------------------
# ranking at one date
# --------------------------------------------------------------------------
def rank_at(D: Data, t: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    i = D.dates.get_loc(t)
    syms = D.uni["symbol"].values
    adj_t = D.adj.iloc[i][syms]
    ok = adj_t.notna()
    fv = D.first_valid[syms]
    ok &= fv.notna() & (fv <= D.dates[max(i - C.MIN_HISTORY, 0)])
    raw_t = D.raw.iloc[i][syms]
    ok &= raw_t >= C.MIN_PRICE
    dv = D.dvol.iloc[max(i - 62, 0):i + 1][syms].median()
    ok &= dv >= C.MIN_DOLLAR_VOL
    u = D.uni[ok.values].copy()
    # market cap: unadjusted price x latest shares known by t-1
    sh = D.shares[D.shares["k"] <= t - DAY]
    sh = sh[sh["cik"].isin(u["cik"])].sort_values("k").drop_duplicates("cik", keep="last")
    u = u.merge(sh[["cik", "shares"]], on="cik", how="left")
    u["mcap"] = u["shares"] * u["symbol"].map(raw_t)
    u = u[u["mcap"] >= C.MIN_MCAP]
    universe = u["symbol"].tolist()

    # fundamentals snapshot: latest quarter whose core numbers were public
    f = D.fund[(D.fund["k_core"] <= t - DAY)
               & (D.fund["qend"] >= t - pd.Timedelta(days=C.MAX_FUND_AGE_DAYS))
               & D.fund["cik"].isin(u["cik"])]
    f = f.sort_values("qid").drop_duplicates("cik", keep="last")
    e = u.merge(f, on="cik", how="inner")
    e = e[(e["cfo_ttm"] > 0) & (e["rev_ttm"] > 0)].copy()
    for s in C.WEIGHTS:
        if f"k_{s}" in e:
            e.loc[e[f"k_{s}"] > t - DAY, s] = np.nan
    # momentum
    a = D.adj[e["symbol"]]
    e["mom_12_1"] = (a.iloc[i - 21] / a.iloc[i - 252] - 1).values
    cl = D.close[e["symbol"]].iloc[i - 251:i + 1]
    e["near_high"] = (cl.iloc[-1] / cl.max()).values
    e = e[e["mom_12_1"].notna()].copy()
    comp = np.zeros(len(e))
    for s, w in C.WEIGHTS.items():
        r = e[s].replace([np.inf, -np.inf], np.nan).rank(pct=True).fillna(0.5)
        e[f"r_{s}"] = r
        comp += w * r.values
    e["score"] = comp
    e = e.sort_values("score", ascending=False).reset_index(drop=True)
    e["rank"] = np.arange(1, len(e) + 1)
    e["date"] = t
    return e, universe


# --------------------------------------------------------------------------
# books
# --------------------------------------------------------------------------
def ibkr_fee(shares: float) -> float:
    return max(C.COMM_MIN, C.COMM_PER_SH * shares) + C.SLIP_PER_SH * shares


class Book:
    """mode 'ibkr' ($3k whole shares) or 'uncon' (fractional, exact EW)."""

    def __init__(self, n: int, mode: str, cash: float):
        self.n, self.mode, self.cash = n, mode, cash
        self.pos: dict[str, dict] = {}     # sym -> units, invested, entry
        self.closed: list[dict] = []
        self.fees = 0.0
        self.traded = 0.0
        self.n_orders = 0
        self.skipped_price = 0

    def value(self, D: Data, t) -> float:
        return self.cash + sum(p["units"] * D.adj_ff.at[t, s] for s, p in self.pos.items())

    def _close(self, s, t, proceeds):
        p = self.pos.pop(s)
        self.closed.append({"symbol": s, "entry": p["entry"], "exit": t,
                            "pnl": proceeds - p["invested"],
                            "ret": proceeds / p["invested"] - 1 if p["invested"] > 0 else np.nan})

    def rebalance(self, D: Data, t, ranked: pd.DataFrame):
        rank = dict(zip(ranked["symbol"], ranked["rank"]))
        # positions whose price series ended: sold at last close, no fee
        for s in [s for s in self.pos if D.last_valid[s] < t]:
            v = self.pos[s]["units"] * D.adj_ff.at[t, s]
            self.cash += v
            self._close(s, D.last_valid[s], v)
        keep = [s for s in self.pos if rank.get(s, 10**9) <= C.BUFFER_MULT * self.n]
        for s in [s for s in self.pos if s not in keep]:
            self._sell(D, t, s)
        keep = sorted(keep, key=lambda s: rank[s])[: self.n]
        for s in [s for s in self.pos if s not in keep]:
            self._sell(D, t, s)
        cands = [s for s in ranked["symbol"] if s not in self.pos]
        if self.mode == "ibkr":
            self._fill_ibkr(D, t, cands)
        else:
            self._fill_uncon(D, t, cands)

    def _sell(self, D, t, s):
        v = self.pos[s]["units"] * D.adj_ff.at[t, s]
        if self.mode == "ibkr":
            shares = v / D.raw_ff.at[t, s]
            fee = ibkr_fee(shares)
        else:
            fee = v * C.UNCON_COST_BPS / 1e4
        self.fees += fee
        self.traded += v
        self.n_orders += 1
        self.cash += v - fee
        self._close(s, t, v - fee)

    def _buy(self, D, t, s, dollars, fee):
        units = dollars / D.adj.at[t, s]
        self.pos[s] = {"units": units, "invested": dollars + fee, "entry": t}
        self.cash -= dollars + fee
        self.fees += fee
        self.traded += dollars
        self.n_orders += 1

    def _fill_ibkr(self, D, t, cands):
        target = self.value(D, t) / self.n
        for s in cands:
            if len(self.pos) >= self.n or self.cash < 0.5 * target:
                break
            p = D.raw.at[t, s]
            sh = math.floor(target / p)
            if sh == 0:
                if p <= C.ONE_SHARE_MAX_MULT * target:
                    sh = 1
                else:
                    self.skipped_price += 1
                    continue
            while sh > 0 and sh * p + ibkr_fee(sh) > self.cash:
                sh -= 1
            if sh == 0:
                self.skipped_price += 1
                continue
            self._buy(D, t, s, sh * p, ibkr_fee(sh))

    def _fill_uncon(self, D, t, cands):
        new = cands[: self.n - len(self.pos)]
        hold = list(self.pos) + new
        if not hold:
            return
        V = self.value(D, t)
        tgt = V * (1 - C.UNCON_COST_BPS / 1e4) / len(hold)
        for s in list(self.pos):      # trims / top-ups
            cur = self.pos[s]["units"] * D.adj.at[t, s]
            d = tgt - cur
            fee = abs(d) * C.UNCON_COST_BPS / 1e4
            self.pos[s]["units"] = tgt / D.adj.at[t, s]
            self.pos[s]["invested"] += d + fee
            self.cash -= d + fee
            self.fees += fee
            self.traded += abs(d)
        for s in new:
            self._buy(D, t, s, tgt, tgt * C.UNCON_COST_BPS / 1e4)


def run_book(D: Data, ranks: dict, rebals: list, start, end, n, mode) -> dict:
    cash = C.START_CASH
    b = Book(n, mode, cash)
    rb = [t for t in rebals if start <= t < end]
    days = D.dates[(D.dates >= start) & (D.dates <= end)]
    vals = pd.Series(index=days, dtype=float)
    bounds = rb + [end]
    for j, t in enumerate(rb):
        b.rebalance(D, t, ranks[t])
        seg = days[(days >= t) & (days <= bounds[j + 1])]
        syms = list(b.pos)
        units = np.array([b.pos[s]["units"] for s in syms])
        px = D.adj_ff.loc[seg, syms].to_numpy() if syms else np.zeros((len(seg), 0))
        vals.loc[seg] = b.cash + px @ units
    # open positions are marked at the final close (no exit fee)
    open_marks = [{"symbol": s, "entry": p["entry"], "exit": pd.NaT,
                   "pnl": p["units"] * D.adj_ff.at[end, s] - p["invested"],
                   "ret": (p["units"] * D.adj_ff.at[end, s] / p["invested"] - 1)
                   if p["invested"] > 0 else np.nan}
                  for s, p in b.pos.items()]
    pos = pd.DataFrame(b.closed + open_marks)
    return {"curve": vals, "positions": pos, "fees": b.fees, "traded": b.traded,
            "orders": b.n_orders, "skipped_price": b.skipped_price}


def ew_universe(D: Data, univ: dict, rebals, start, end) -> pd.Series:
    rb = [t for t in rebals if start <= t < end]
    days = D.dates[(D.dates >= start) & (D.dates <= end)]
    vals = pd.Series(index=days, dtype=float)
    V = C.START_CASH
    bounds = rb + [end]
    for j, t in enumerate(rb):
        syms = univ[t]
        seg = days[(days >= t) & (days <= bounds[j + 1])]
        px = D.adj_ff.loc[seg, syms]
        rel = px / px.iloc[0]
        vals.loc[seg] = V * rel.mean(axis=1).to_numpy()
        V = vals.loc[seg[-1]]
    return vals


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def max_dd(v: pd.Series) -> float:
    return float((v / v.cummax() - 1).min())


def cagr(v: pd.Series) -> float:
    yrs = (v.index[-1] - v.index[0]).days / 365.25
    return float((v.iloc[-1] / v.iloc[0]) ** (1 / yrs) - 1)


def spy_shy_mix(D: Data, start, end, target_dd: float) -> tuple[float, pd.Series]:
    px = D.adj.loc[start:end, ["SPY", "SHY"]].ffill()
    r = px.pct_change().fillna(0)
    month = r.index.to_period("M")

    def curve(w):
        out, V = [], 1.0
        for _, g in r.groupby(month):
            # buy-and-hold within the month, rebalance at month start
            rel = (1 + g).cumprod()
            seg = V * (w * rel["SPY"] + (1 - w) * rel["SHY"])
            out.append(seg)
            V = seg.iloc[-1]
        return pd.concat(out) * C.START_CASH

    if max_dd(curve(1.0)) >= target_dd:     # book drew down more than SPY
        return 1.0, curve(1.0)
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if max_dd(curve(mid)) > target_dd:
            lo = mid
        else:
            hi = mid
    w = (lo + hi) / 2
    return w, curve(w)


def concentration(pos: pd.DataFrame) -> dict:
    p = pos["pnl"].sort_values(ascending=False).to_numpy()
    tot = p.sum()
    out = {"n_positions": int(len(p)), "total_pnl": float(tot)}
    for pct in (1, 5):
        k = max(1, math.ceil(len(p) * pct / 100))
        out[f"top{pct}pct_n"] = k
        out[f"top{pct}pct_share"] = float(p[:k].sum() / tot) if tot > 0 else None
    out["hit_rate"] = float((pos["pnl"] > 0).mean()) if len(p) else None
    return out


def quarterly_beat(v: pd.Series, spy: pd.Series, rebals) -> float:
    pts = [t for t in rebals if v.index[0] <= t <= v.index[-1]] + [v.index[-1]]
    pts = sorted(set(pts))
    w = 0
    for a, b in zip(pts[:-1], pts[1:]):
        w += (v[b] / v[a]) > (spy[b] / spy[a])
    return w / (len(pts) - 1)


def summarize(D, res, spy_curve, rebals, start, end) -> dict:
    v = res["curve"]
    yrs = (v.index[-1] - v.index[0]).days / 365.25
    dd = max_dd(v)
    w, mix = spy_shy_mix(D, start, end, dd)
    out = {"cagr": cagr(v), "max_dd": dd, "ret_dd": cagr(v) / abs(dd) if dd else None,
           "end_value": float(v.iloc[-1]),
           "quarters_beat_spy": quarterly_beat(v, spy_curve, rebals),
           "turnover_pa": res["traded"] / 2 / v.mean() / yrs,
           "fees_total": res["fees"], "fees_pct_pa": res["fees"] / v.mean() / yrs,
           "orders": res["orders"], "skipped_price": res["skipped_price"],
           "mix_spy_weight": w, "mix_cagr": cagr(mix), "mix_max_dd": max_dd(mix)}
    out.update(concentration(res["positions"]))
    return out


def bench_summary(v: pd.Series) -> dict:
    dd = max_dd(v)
    return {"cagr": cagr(v), "max_dd": dd, "ret_dd": cagr(v) / abs(dd),
            "end_value": float(v.iloc[-1])}


# --------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    D = Data()
    rebals = D.rebal_dates()
    log.info("%d rebalances %s .. %s", len(rebals), rebals[0].date(), rebals[-1].date())
    ranks, univ = {}, {}
    for t in rebals:
        ranks[t], univ[t] = rank_at(D, t)
    allr = pd.concat(ranks.values(), ignore_index=True)
    allr.to_parquet(C.RESULTS / "rankings.parquet", index=False)
    cov = pd.DataFrame({"date": rebals,
                        "universe": [len(univ[t]) for t in rebals],
                        "eligible": [len(ranks[t]) for t in rebals]})
    log.info("universe/eligible per rebalance:\n%s", cov.to_string())

    end = D.dates[-1]
    hold = pd.Timestamp(C.HOLDOUT_START)
    periods = {"development": (rebals[0], hold), "holdout": (hold, end),
               "full": (rebals[0], end)}
    metrics, curves, positions = {}, {}, []
    for pname, (a, b) in periods.items():
        spy = D.adj.loc[a:b, "SPY"]
        spy_c = spy / spy.iloc[0] * C.START_CASH
        ew = ew_universe(D, univ, rebals, a, b)
        metrics[f"{pname}|SPY"] = bench_summary(spy_c)
        metrics[f"{pname}|EW universe"] = bench_summary(ew)
        curves[f"{pname}|SPY"] = spy_c
        curves[f"{pname}|EW universe"] = ew
        for bname, n in C.BOOKS.items():
            for mode in ("ibkr", "uncon"):
                key = f"{pname}|{bname}-{n} {mode}"
                res = run_book(D, ranks, rebals, a, b, n, mode)
                metrics[key] = summarize(D, res, spy, rebals, a, b)
                curves[key] = res["curve"]
                pos = res["positions"].assign(period=pname, book=f"{bname}-{n} {mode}")
                positions.append(pos)
                log.info("%s: %s", key, {k: round(v, 3) if isinstance(v, float) else v
                                         for k, v in metrics[key].items()})
    pd.DataFrame(curves).to_parquet(C.RESULTS / "curves.parquet")
    pd.concat(positions, ignore_index=True).to_parquet(C.RESULTS / "positions.parquet",
                                                      index=False)
    cov.to_parquet(C.RESULTS / "coverage.parquet", index=False)
    meta = {"rebalances": len(rebals), "first": str(rebals[0].date()),
            "last": str(rebals[-1].date()), "end": str(end.date()),
            "n_fin_excluded": D.n_fin_excluded, "n_companies": len(D.uni)}
    (C.RESULTS / "metrics.json").write_text(json.dumps(
        {"meta": meta, "metrics": metrics}, indent=1, default=str))
    log.info("done")


if __name__ == "__main__":
    main()
