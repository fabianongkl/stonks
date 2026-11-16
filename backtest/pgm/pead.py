"""Post-earnings announcement drift backtest.

    python -m backtest.pgm.pead

Rules: docs/PEAD_SCREEN.md (frozen 2026-11-16). Reuses the PGM price panels,
universe filters, cost model and metrics from backtest/pgm/engine.py.
Writes backtest/pgm/results/pead_*.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from . import config as C
from . import engine as E

log = logging.getLogger("pgm.pead")
RAW = C.CACHE / "pead_events_raw.parquet"
HOLD_DAYS = 60
WINDOW = 63
MIN_EVENTS = 200
SLOTS = {"slots-20": 20, "slots-8": 8}
START = pd.Timestamp("2013-03-01")


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
def build_events(D: E.Data) -> pd.DataFrame:
    r = pd.read_parquet(RAW)
    r = r[r["form"] != "_none"].copy()
    r["fdate"] = pd.to_datetime(r["filingDate"])
    acc = pd.to_datetime(r["acceptanceDateTime"].str.replace("Z", "", regex=False),
                         errors="coerce")
    ny = acc.dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    r["after_close"] = (ny.dt.hour >= 16).fillna(False).to_numpy()
    r["is202"] = (r["form"] == "8-K") & r["items"].str.contains(r"\b2\.02\b", regex=True)
    e8 = r[r["is202"]][["cik", "fdate", "after_close"]].assign(src="8-K 2.02")
    per = r[r["form"].isin(["10-Q", "10-K", "10-KT"])][["cik", "fdate", "after_close"]]
    # fallback: periodic report only if no 2.02 8-K in the prior 60 days
    m = pd.merge_asof(per.sort_values("fdate"),
                      e8[["cik", "fdate"]].rename(columns={"fdate": "last8k"})
                      .sort_values("last8k"),
                      left_on="fdate", right_on="last8k", by="cik", direction="backward")
    gap = (m["fdate"] - m["last8k"]).dt.days
    per = m[m["last8k"].isna() | (gap > 60)][["cik", "fdate", "after_close"]] \
        .assign(src="10-Q/10-K")
    ev = pd.concat([e8, per]).sort_values(["cik", "fdate"]).reset_index(drop=True)
    # drop events < 30 days after the previous kept event of the same company
    keep, last = [], {}
    for cik, d in zip(ev["cik"].to_numpy(), ev["fdate"].to_numpy()):
        ok = cik not in last or (d - last[cik]) >= np.timedelta64(30, "D")
        keep.append(ok)
        if ok:
            last[cik] = d
    ev = ev[keep]
    ev = ev[ev["fdate"] >= START - pd.Timedelta(days=10)]
    # day 0 (index into trading dates)
    dates = D.dates.values
    i0 = np.searchsorted(dates, ev["fdate"].values, side="left")
    same_day = (i0 < len(dates)) & (dates[np.minimum(i0, len(dates) - 1)] == ev["fdate"].values)
    i0 = i0 + (same_day & ev["after_close"].to_numpy()).astype(int)
    ev["i0"] = i0
    ev["cik"] = ev["cik"].astype(int)
    ev["symbol"] = ev["cik"].map({c: s for s, c in D.sym2cik.items()})
    ev = ev.dropna(subset=["symbol"])
    ev = ev[(ev["i0"] >= 252) & (ev["i0"] + 2 < len(dates))].reset_index(drop=True)
    return ev


def score_events(D: E.Data, ev: pd.DataFrame) -> pd.DataFrame:
    cols = {s: k for k, s in enumerate(D.adj.columns)}
    A = D.adj.to_numpy()
    Aff = D.adj_ff.to_numpy()
    RAW_ = D.raw.to_numpy()
    spy = A[:, cols["SPY"]]
    c = ev["symbol"].map(cols).to_numpy()
    i0 = ev["i0"].to_numpy()
    n = len(D.dates)
    react = A[i0 + 1, c] / A[i0 - 2, c] - 1
    react -= spy[i0 + 1] / spy[i0 - 2] - 1
    ev["react"] = react
    # universe filters at day 0
    fv = pd.to_datetime(D.first_valid.reindex(ev["symbol"])).to_numpy()
    ok = pd.notna(fv) & (fv <= D.dates.values[i0 - 252])
    ok &= RAW_[i0, c] >= C.MIN_PRICE
    med = D.dvol.rolling(63, min_periods=40).median().to_numpy()
    ok &= med[i0, c] >= C.MIN_DOLLAR_VOL
    ev["day0"] = D.dates.values[i0]
    sh = D.shares.sort_values("k")
    q = ev[["cik", "day0"]].assign(kq=ev["day0"] - pd.Timedelta(days=1)) \
        .reset_index().sort_values("kq")
    q = pd.merge_asof(q, sh, left_on="kq", right_on="k", by="cik").set_index("index")
    ev["mcap"] = q["shares"].reindex(ev.index).to_numpy() * RAW_[i0, c]
    ok &= ev["mcap"].to_numpy() >= C.MIN_MCAP
    ok &= np.isfinite(react)
    ok &= np.isfinite(A[np.minimum(i0 + 2, n - 1), c])     # priced on entry day
    ev = ev[ok].copy()
    # trailing 90th percentile cut-off by day +1
    ev["ir"] = ev["i0"] + 1
    by_day = ev.groupby("ir")["react"].apply(np.array)
    days = by_day.index.to_numpy()
    cut = {}
    for d in np.unique(ev["ir"]):
        lo = np.searchsorted(days, d - WINDOW + 1)
        hi = np.searchsorted(days, d, side="right")
        pool = np.concatenate(by_day.iloc[lo:hi].to_list()) if hi > lo else np.array([])
        cut[d] = np.quantile(pool, 0.9) if len(pool) >= MIN_EVENTS else np.nan
    ev["cut"] = ev["ir"].map(cut)
    ev["top"] = ev["react"] >= ev["cut"]
    ev["ie"] = ev["i0"] + 2
    ev["ix"] = np.minimum(ev["ie"] + HOLD_DAYS, n - 1)
    c = ev["symbol"].map(cols).to_numpy()
    ev["fwd"] = Aff[ev["ix"], c] / A[ev["ie"], c] - 1
    ev["fwd_abn"] = ev["fwd"] - (spy[ev["ix"]] / spy[ev["ie"]] - 1)
    ev["entry"] = D.dates.values[ev["ie"]]
    return ev.reset_index(drop=True)


# --------------------------------------------------------------------------
# books
# --------------------------------------------------------------------------
class SlotBook(E.Book):
    def step(self, D, t, i, todays: pd.DataFrame):
        for s in [s for s, p in self.pos.items() if p["exit_i"] <= i]:
            if D.last_valid[s] < t:
                v = self.pos[s]["units"] * D.adj_ff.at[t, s]
                self.cash += v
                self._close(s, D.last_valid[s], v)
            else:
                self._sell(D, t, s)
        if todays is None or len(self.pos) >= self.n:
            return
        target = self.value(D, t) / self.n
        for s, xi in zip(todays["symbol"], todays["ix"]):
            if len(self.pos) >= self.n:
                break
            if s in self.pos:
                continue
            if self.mode == "ibkr":
                if self.cash < 0.5 * target:
                    break
                p = D.raw.at[t, s]
                sh = int(target // p)
                if sh == 0:
                    if p <= C.ONE_SHARE_MAX_MULT * target:
                        sh = 1
                    else:
                        self.skipped_price += 1
                        continue
                while sh > 0 and sh * p + E.ibkr_fee(sh) > self.cash:
                    sh -= 1
                if sh == 0:
                    self.skipped_price += 1
                    continue
                self._buy(D, t, s, sh * p, E.ibkr_fee(sh))
            else:
                dollars = min(target, self.cash / (1 + C.UNCON_COST_BPS / 1e4))
                if dollars < 0.5 * target:
                    break
                self._buy(D, t, s, dollars, dollars * C.UNCON_COST_BPS / 1e4)
            self.pos[s]["exit_i"] = xi


def run_slots(D, sig, start, end, n, mode) -> dict:
    b = SlotBook(n, mode, C.START_CASH)
    days = D.dates[(D.dates >= start) & (D.dates <= end)]
    groups = {k: g.sort_values("react", ascending=False)
              for k, g in sig[(sig["entry"] >= start) & (sig["entry"] <= end)].groupby("ie")}
    vals = np.empty(len(days))
    for j, t in enumerate(days):
        i = D.dates.get_loc(t)
        b.step(D, t, i, groups.get(i))
        vals[j] = b.value(D, t)
    marks = [{"symbol": s, "entry": p["entry"], "exit": pd.NaT,
              "pnl": p["units"] * D.adj_ff.at[end, s] - p["invested"], "ret": np.nan}
             for s, p in b.pos.items()]
    return {"curve": pd.Series(vals, index=days),
            "positions": pd.DataFrame(b.closed + marks), "fees": b.fees,
            "traded": b.traded, "orders": b.n_orders, "skipped_price": b.skipped_price}


def run_all_signals(D, sig, start, end) -> dict:
    days = D.dates[(D.dates >= start) & (D.dates <= end)]
    R = D.adj_ff.pct_change().to_numpy()
    cols = {s: k for k, s in enumerate(D.adj.columns)}
    s = sig[(sig["entry"] >= start) & (sig["entry"] <= end)]
    by_entry = {k: list(zip(g["symbol"].map(cols), g["ix"])) for k, g in s.groupby("ie")}
    active: list[tuple[int, int]] = []
    V, vals, fees, traded = C.START_CASH, [], 0.0, 0.0
    bps = C.UNCON_COST_BPS / 1e4
    for t in days:
        i = D.dates.get_loc(t)
        if active:
            r = np.nan_to_num(R[i, [c for c, _ in active]])
            V *= 1 + r.mean()
        n_before = len(active)
        exits = [a for a in active if a[1] <= i]
        active = [a for a in active if a[1] > i]
        new = by_entry.get(i, [])
        active += new
        cost = 0.0
        if exits and n_before:
            cost += bps * len(exits) / n_before
            traded += V * len(exits) / n_before
        if new and active:
            cost += bps * len(new) / len(active)
            traded += V * len(new) / len(active)
        fees += V * cost
        V *= 1 - cost
        vals.append(V)
    return {"curve": pd.Series(vals, index=days), "fees": fees, "traded": traded}


def mix_curve(D, start, end, w) -> pd.Series:
    px = D.adj.loc[start:end, ["SPY", "SHY"]].ffill()
    r = px.pct_change().fillna(0)
    out, V = [], 1.0
    for _, g in r.groupby(r.index.to_period("M")):
        rel = (1 + g).cumprod()
        seg = V * (w * rel["SPY"] + (1 - w) * rel["SHY"])
        out.append(seg)
        V = seg.iloc[-1]
    return pd.concat(out) * C.START_CASH


# --------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    D = E.Data()
    ev = build_events(D)
    log.info("events after mapping: %d (%s)", len(ev), ev["src"].value_counts().to_dict())
    ev = score_events(D, ev)
    ev = ev[ev["entry"] >= START]
    ev.to_parquet(C.RESULTS / "pead_events.parquet", index=False)
    sig = ev[ev["top"]]
    log.info("universe events %d, top-decile signals %d", len(ev), len(sig))

    end = D.dates[-1]
    hold = pd.Timestamp(C.HOLDOUT_START)
    periods = {"development": (START, hold - pd.Timedelta(days=1)),
               "holdout": (hold, end), "full": (START, end)}
    dev_end = D.dates[D.dates < hold][-1]
    periods["development"] = (START, dev_end)

    # diagnostic: 60-day abnormal return by reaction decile (calendar-quarter deciles)
    ev["q"] = pd.to_datetime(ev["entry"]).dt.to_period("Q")
    ev["dec"] = ev.groupby("q")["react"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 10, labels=False) + 1)
    ev["period"] = np.where(ev["entry"] >= hold, "holdout", "development")
    diag = ev.groupby(["period", "dec"])["fwd_abn"].agg(["mean", "median", "count"])
    top = ev[ev["top"]].groupby("period")["fwd_abn"].agg(["mean", "median", "count"])

    rebals = D.rebal_dates()
    univ = {t: E.rank_at(D, t)[1] for t in rebals}
    metrics, curves, positions, weights = {}, {}, [], {}
    for pname, (a, b) in periods.items():
        spy = D.adj.loc[a:b, "SPY"]
        spy_c = spy / spy.iloc[0] * C.START_CASH
        curves[f"{pname}|SPY"] = spy_c
        metrics[f"{pname}|SPY"] = E.bench_summary(spy_c)
        prior = max(t for t in rebals if t <= a)
        univ2 = {**univ, a: univ[prior]}
        rebals2 = [a] + [t for t in rebals if t > a]
        ew = E.ew_universe(D, univ2, rebals2, a, b)
        curves[f"{pname}|EW universe"] = ew
        metrics[f"{pname}|EW universe"] = E.bench_summary(ew)
        books = {}
        for bname, n in SLOTS.items():
            for mode in ("ibkr", "uncon"):
                books[f"{bname} {mode}"] = run_slots(D, sig, a, b, n, mode)
        books["all-signals"] = run_all_signals(D, sig, a, b)
        for key, res in books.items():
            v = res["curve"]
            yrs = (v.index[-1] - v.index[0]).days / 365.25
            dd = E.max_dd(v)
            if pname == "development":
                w, _ = E.spy_shy_mix(D, a, b, dd)
                weights[key] = w
            w = weights[key]
            mix = mix_curve(D, a, b, w)
            m = {"cagr": E.cagr(v), "max_dd": dd, "ret_dd": E.cagr(v) / abs(dd),
                 "end_value": float(v.iloc[-1]),
                 "quarters_beat_spy": E.quarterly_beat(v, spy, rebals),
                 "turnover_pa": res["traded"] / 2 / v.mean() / yrs,
                 "fees_total": res["fees"], "fees_pct_pa": res["fees"] / v.mean() / yrs,
                 "orders": res.get("orders", 0), "skipped_price": res.get("skipped_price", 0),
                 "mix_spy_weight": w, "mix_cagr": E.cagr(mix), "mix_max_dd": E.max_dd(mix)}
            if "positions" in res:
                m.update(E.concentration(res["positions"]))
                positions.append(res["positions"].assign(period=pname, book=key))
            else:
                m.update({"top1pct_share": None, "top5pct_share": None, "top1pct_n": 0,
                          "top5pct_n": 0, "hit_rate": None, "n_positions": None})
            metrics[f"{pname}|{key}"] = m
            curves[f"{pname}|{key}"] = v
            log.info("%s|%s: %s", pname, key,
                     {k: round(x, 3) if isinstance(x, float) else x for k, x in m.items()})
    pd.DataFrame(curves).to_parquet(C.RESULTS / "pead_curves.parquet")
    if positions:
        pd.concat(positions, ignore_index=True).to_parquet(
            C.RESULTS / "pead_positions.parquet", index=False)
    meta = {"events": int(len(ev)), "signals": int(len(sig)),
            "src": ev["src"].value_counts().to_dict(),
            "signals_by_period": ev[ev["top"]].groupby("period").size().to_dict(),
            "dev_fixed_mix_weights": weights,
            "decile_diag": {f"{p}|{d}": r for (p, d), r in
                            diag.round(4).to_dict("index").items()},
            "top_signal_fwd_abn": top.round(4).to_dict("index")}
    (C.RESULTS / "pead_metrics.json").write_text(json.dumps(
        {"meta": meta, "metrics": metrics}, indent=1, default=str))
    log.info("decile diagnostic:\n%s\ntop-decile signals:\n%s", diag, top)
    log.info("done")


if __name__ == "__main__":
    main()
