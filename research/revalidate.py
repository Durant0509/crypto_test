"""
Re-validate everything through the FIXED walk-forward (research/wf.py), which
reports OOS-2023 AND full-period-2022 side by side. Confirms:
  (a) the L/S mainline (was it inflated by skipping 2022? hypothesis: no, it's
      market-neutral and earns in the bear market), and
  (b) the momentum factor's true regime dependence (the gap that started this).

    python research/revalidate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from research.wf import evaluate, fmt                                    # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params, compute,    # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
PCT_WIN_D = 45


def ls_builder(df):
    return compute(df, Params(lookback_hours=PCT_WIN_D * 24))


def mom_builder(df):
    out = df[["open", "high", "low", "close"]].copy()
    p = Params()
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    mom = df["close"].pct_change(30 * 24)
    pct = percentile_rank(mom, PCT_WIN_D * 24)
    out["pct"] = pct
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG
    t[pct <= 0.10] = SHORT
    t[pct.isna()] = FLAT
    out["target"] = t
    lo = df["close"].rolling(5 * 24, min_periods=5 * 24).min().shift(1)
    hi = df["close"].rolling(5 * 24, min_periods=5 * 24).max().shift(1)
    out["exit_long"] = (df["close"] < lo).fillna(False)
    out["exit_short"] = (df["close"] > hi).fillna(False)
    return out


def main():
    p45 = Params(lookback_hours=PCT_WIN_D * 24)
    print("=" * 100)
    print("L/S REVERSION mainline — fixed harness (OOS-2023 vs FULL-2022)")
    print("=" * 100)
    for sym in COINS:
        df = build_hourly(sym)
        # fixed 3d (all coins) and normalize (BTC/ADA adopted)
        c_time = evaluate(df, ls_builder, dict(params=p45, exit_mode="time"))
        print(fmt(c_time, f"{sym} L/S 3d"))
        print("   yearly% (full):", c_time["full_2022"]["yearly_pct"])
        if sym in ("BTCUSDT", "ADAUSDT"):
            c_norm = evaluate(df, ls_builder,
                              dict(params=Params(lookback_hours=PCT_WIN_D * 24, hold_hours=5 * 24),
                                   exit_mode="normalize", neutral_lo=0.40, neutral_hi=0.60,
                                   min_hold_hours=24))
            print(fmt(c_norm, f"{sym} L/S normalize"))

    print("\n" + "=" * 100)
    print("MOMENTUM factor — fixed harness (the one that exposed the bug)")
    print("=" * 100)
    df = build_hourly("BTCUSDT")
    cap = Params(hold_hours=30 * 24)
    cmom = evaluate(df, mom_builder, dict(params=cap, exit_mode="signal", min_hold_hours=24))
    print(fmt(cmom, "BTC momentum Donch-5"))
    print("   yearly% (full):", cmom["full_2022"]["yearly_pct"])


if __name__ == "__main__":
    main()
