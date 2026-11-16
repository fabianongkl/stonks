"""Turn results/metrics.json into markdown tables (printed + saved).

    python -m backtest.pgm.report
"""

from __future__ import annotations

import json

import pandas as pd

from . import config as C


def pct(x, d=1):
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.{d}f}%"


def main() -> None:
    m = json.loads((C.RESULTS / "metrics.json").read_text())["metrics"]
    lines = []
    for period in ("development", "holdout", "full"):
        lines.append(f"\n### {period}\n")
        lines.append("| Book | CAGR | Worst DD | Ret/DD | End $ | SPY/SHY mix at same DD (w SPY, CAGR) | Qtrs beat SPY | Hit rate | Top 1% share of P&L | Top 5% share | Turnover/yr | Fees $ (%/yr) | Orders |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, v in m.items():
            p, name = k.split("|")
            if p != period:
                continue
            if "mix_cagr" in v:
                lines.append(
                    f"| {name} | {pct(v['cagr'])} | {pct(v['max_dd'])} | {v['ret_dd']:.2f} | "
                    f"{v['end_value']:,.0f} | {v['mix_spy_weight']:.2f}, {pct(v['mix_cagr'])} | "
                    f"{pct(v['quarters_beat_spy'], 0)} | {pct(v['hit_rate'], 0)} | "
                    f"{pct(v['top1pct_share'], 0)} (n={v['top1pct_n']}) | "
                    f"{pct(v['top5pct_share'], 0)} (n={v['top5pct_n']}) | "
                    f"{pct(v['turnover_pa'], 0)} | {v['fees_total']:,.0f} ({pct(v['fees_pct_pa'], 2)}) | "
                    f"{v['orders']} |")
            else:
                lines.append(f"| {name} | {pct(v['cagr'])} | {pct(v['max_dd'])} | "
                             f"{v['ret_dd']:.2f} | {v['end_value']:,.0f} | | | | | | | | |")
    md = "\n".join(lines)
    (C.RESULTS / "tables.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
