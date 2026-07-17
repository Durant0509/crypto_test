"""
Experiment #10 — Monte-Carlo trade-shuffle robustness for the no-stop L/S sleeve.

WHY (the session's defensive priority): the L/S strategy runs NO stop-loss with a
~52% win rate. The historical MaxDD (-14.8% on BTC) is just ONE realized ORDER of
trades. If the SAME trades had occurred in a different sequence, how deep could the
drawdown have been? A run of losers clustered together is what blows up a no-stop
book. This bounds the tail we can't see in the single historical path, and directly
informs safe leverage (a levered account is liquidated at ~ -1/L).

Method: take the sequence of per-trade returns from the full-period backtest
(incl. 2022), shuffle the ORDER 1000x, rebuild a fixed-notional equity path each
time, record the MaxDD distribution + worst-run stats. (Shuffling order preserves
the trade P&L set but destroys the specific sequencing — isolates path/clustering
risk.) Reported per whitelist coin.

    python research/monte_carlo.py
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
N_SHUFFLES = 1000
BASE = 1000.0
START, END = "2022-01-01", "2026-06-30"


def trade_returns(sym):
    df = build_hourly(sym)
    cfg = BacktestConfig(start=START, end=END,
                         params=Params(lookback_hours=PCT_WIN_D * 24), exit_mode="time")
    res = run(df, cfg)
    # per-trade P&L on the fixed base notional -> additive equity units
    return res.trades["pnl"].to_numpy(dtype=float) / BASE, res


def path_maxdd(rets):
    """Fixed-notional additive equity path MaxDD for a sequence of trade returns."""
    eq = BASE + np.cumsum(rets) * BASE
    eq = np.concatenate([[BASE], eq])
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min())


def main():
    # deterministic shuffles without Math.random-style nondeterminism: seed a local RNG
    rng = np.random.default_rng(12345)
    print("=" * 78)
    print(f"Monte-Carlo trade-order shuffle ({N_SHUFFLES}x) — no-stop L/S sleeve, full period")
    print("=" * 78)
    print(f"  {'coin':<6} {'trades':>6} {'histDD%':>8} {'medDD%':>8} {'p95DD%':>8} "
          f"{'p99DD%':>8} {'worstDD%':>9} {'P(ruin>50%)':>11}")
    for sym in COINS:
        rets, res = trade_returns(sym)
        n = len(rets)
        hist_dd = path_maxdd(rets) * 100
        dds = np.empty(N_SHUFFLES)
        for i in range(N_SHUFFLES):
            dds[i] = path_maxdd(rng.permutation(rets))
        dds *= 100
        med, p95, p99, worst = np.percentile(dds, 50), np.percentile(dds, 5), \
            np.percentile(dds, 1), dds.min()
        p_ruin = float((dds <= -50.0).mean()) * 100
        print(f"  {sym.replace('USDT',''):<6} {n:>6} {hist_dd:>8.1f} {med:>8.1f} "
              f"{p95:>8.1f} {p99:>8.1f} {worst:>9.1f} {p_ruin:>10.1f}%")
    print("\n  histDD = the single realized historical path; med/p95/p99 = shuffle percentiles")
    print("  (p95 = 5th-percentile-worst, p99 = 1st-percentile-worst). P(ruin) = shuffles with DD<=-50%.")
    print("  Safe leverage guide: a path hitting -X% would liquidate leverage >= 1/(X/100).")


if __name__ == "__main__":
    main()
