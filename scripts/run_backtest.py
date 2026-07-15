"""
Run the Retail Long/Short-Ratio Reversion backtest on the downloaded 5y dataset
and emit: a per-trade CSV, a metrics JSON, a text summary, and three charts
(equity vs buy&hold, yearly returns, underwater/drawdown).

    python scripts/run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import (                            # noqa: E402
    drawdown_series, format_summary, summary,
)

DATA = ROOT / "data" / "hourly.parquet"
RESULTS = ROOT / "results"


def main():
    df = pd.read_parquet(DATA)
    cfg = BacktestConfig(start="2022-01-01", end="2026-06-30")
    res = run(df, cfg)
    s = summary(res, df["close"])

    RESULTS.mkdir(exist_ok=True)
    res.trades.to_csv(RESULTS / "trades.csv", index=False)
    (RESULTS / "metrics.json").write_text(json.dumps(s, indent=2, default=str))
    txt = format_summary(s)
    (RESULTS / "summary.txt").write_text(txt)
    print(txt)

    # buy & hold curve on same notional basis
    base = cfg.base_notional
    px = df["close"].loc[(df.index >= res.equity.index[0]) & (df.index <= res.equity.index[-1])]
    bh = base * (px / px.iloc[0])

    # --- chart 1: equity vs buy&hold (as cumulative return %) ---------------
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(res.equity.index, (res.equity / base - 1) * 100, color="#0f6674", lw=1.6, label="Strategy (sim)")
    ax.plot(bh.index, (bh / base - 1) * 100, color="#9aa7ad", lw=1.2, ls="--", label="Buy & hold BTC")
    ax.axhline(0, color="#ccc", lw=0.8)
    ax.set_ylabel("Cumulative return (%)")
    ax.set_title("Retail L/S Reversion — cumulative return (simulated)")
    ax.legend(loc="upper left")
    fig.tight_layout(); fig.savefig(RESULTS / "equity.png", dpi=130); plt.close(fig)

    # --- chart 2: yearly returns -------------------------------------------
    yrs = sorted(s["yearly"])
    strat = [s["yearly"][y].get("strategy", 0) * 100 for y in yrs]
    bhy = [s["yearly"][y].get("buy_hold", 0) * 100 for y in yrs]
    x = range(len(yrs))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([i - 0.2 for i in x], strat, width=0.4, color="#0f6674", label="Strategy (sim)")
    ax.bar([i + 0.2 for i in x], bhy, width=0.4, color="#9aa7ad", label="Buy & hold BTC")
    ax.axhline(0, color="#333", lw=0.8)
    ax.set_xticks(list(x)); ax.set_xticklabels(yrs)
    ax.set_ylabel("Annual return (%)"); ax.set_title("Yearly returns")
    for i, v in zip(x, strat):
        ax.text(i - 0.2, v + (2 if v >= 0 else -5), f"{v:+.0f}", ha="center", fontsize=8)
    ax.legend()
    fig.tight_layout(); fig.savefig(RESULTS / "yearly.png", dpi=130); plt.close(fig)

    # --- chart 3: underwater / drawdown ------------------------------------
    dd = drawdown_series(res.equity) * 100
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.fill_between(dd.index, dd.values, 0, color="#c0392b", alpha=0.35)
    ax.plot(dd.index, dd.values, color="#c0392b", lw=0.8)
    ax.set_ylabel("Drawdown (%)"); ax.set_title("Underwater (distance from equity peak)")
    fig.tight_layout(); fig.savefig(RESULTS / "drawdown.png", dpi=130); plt.close(fig)

    print(f"\nartifacts -> {RESULTS}/  (trades.csv, metrics.json, summary.txt, equity.png, yearly.png, drawdown.png)")


if __name__ == "__main__":
    main()
