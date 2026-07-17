"""
Experiment #9 — funding as a COST on the perp holds (realism gap, per SOP).

The backtest/live charge taker fee + slippage per side but NOT funding, even
though the strategy holds perpetual positions for ~3 days. SOP phase 1 says count
funding on perp holds. This makes P&L 擬真 (more realistic), likely LOWERING
Sharpe — this is a truth-teller, not a Sharpe-raiser.

Funding mechanics (verified): the dump stores the 8h rate ffilled hourly (3
distinct values/day at 00/08/16 UTC). A LONG pays funding when the rate is
positive (84.7% of the time on BTC), a SHORT receives it. So per 8h settlement
while holding: pnl_adj -= pos_side * funding_rate * notional. We settle ONLY on
the 8h boundaries a position is open across (not hourly — that would 8x it).

Compares baseline (no funding) vs funding-charged, full period incl. 2022, via a
post-hoc adjustment to each trade using the hours it was actually held.

    python research/funding_cost.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from src.backtest.engine import BacktestConfig, run                     # noqa: E402
from src.strategy.signal import Params                                   # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
PCT_WIN_D = 45
BASE = 1000.0
START, END = "2022-01-01", "2026-06-30"
CACHE = ROOT / "data" / "research"


def funding_settlements(fund: pd.Series):
    """The 8h-boundary funding rates only (00/08/16 UTC), i.e. one value per settle."""
    f = fund.dropna()
    return f[f.index.hour.isin([0, 8, 16])]


def sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def eval_coin(sym):
    df = build_hourly(sym)
    cfg = BacktestConfig(start=START, end=END,
                         params=Params(lookback_hours=PCT_WIN_D * 24), exit_mode="time")
    res = run(df, cfg)
    tr = res.trades.copy()
    fund = pd.read_parquet(CACHE / f"{sym}_funding.parquet")["funding"]
    settles = funding_settlements(fund)

    # per-trade funding cost = sum of settlement rates in [entry, exit) * side * notional
    costs = []
    for _, t in tr.iterrows():
        s = settles[(settles.index >= t["entry_time"]) & (settles.index < t["exit_time"])]
        side = 1 if t["side"] == "LONG" else -1
        # long pays positive funding -> cost; short receives -> negative cost
        fee = side * float(s.sum()) * t["notional"]
        costs.append(fee)
    tr["funding_cost"] = costs
    tr["pnl_after_funding"] = tr["pnl"] - tr["funding_cost"]

    # rebuild daily returns with the funding adjustment applied at each exit day
    exit_day = pd.to_datetime(tr["exit_time"]).dt.tz_convert("UTC").dt.floor("D")
    fund_by_day = tr.groupby(exit_day)["funding_cost"].sum()

    daily_eq = res.equity.resample("1D").last().dropna()
    base_dret = daily_eq.diff().fillna(daily_eq.iloc[0] - BASE) / BASE
    adj = fund_by_day.reindex(base_dret.index).fillna(0.0) / BASE
    aftr_dret = base_dret - adj

    tot_fund = tr["funding_cost"].sum()
    return {
        "coin": sym.replace("USDT", ""),
        "sharpe_base": round(sharpe(base_dret), 2),
        "sharpe_funded": round(sharpe(aftr_dret), 2),
        "total_base_pct": round(float(base_dret.sum()) * 100, 1),
        "total_funded_pct": round(float(aftr_dret.sum()) * 100, 1),
        "total_funding_cost": round(tot_fund, 1),
        "avg_funding_per_trade": round(tot_fund / len(tr), 2) if len(tr) else 0.0,
        "avg_funding_pct_notional": round(float((tr["funding_cost"] / tr["notional"]).mean()) * 100, 3),
        "n_trades": len(tr),
    }


def main():
    print("=" * 84)
    print("Funding-as-cost on 3-day perp holds — realism gap (full period incl. 2022)")
    print("=" * 84)
    print(f"  {'coin':<6} {'Sharpe base':>11} {'Sharpe +fund':>13} {'total base%':>12} "
          f"{'total +fund%':>13} {'avg fund/trade':>15} {'%notional':>10}")
    for sym in COINS:
        r = eval_coin(sym)
        print(f"  {r['coin']:<6} {r['sharpe_base']:>11} {r['sharpe_funded']:>13} "
              f"{r['total_base_pct']:>11}% {r['total_funded_pct']:>12}% "
              f"{r['avg_funding_per_trade']:>13} U {r['avg_funding_pct_notional']:>9}%")
    print("\n  Funding charged only at 8h settlements a position is open across.")
    print("  Long pays positive funding (84.7% of the time); short receives it.")
    print("  Note: this strat is ~half long / half short, so funding partly nets out.")


if __name__ == "__main__":
    main()
