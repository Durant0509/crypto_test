"""
Experiment #21 — price-CONDITION exits for the L/S reversion sleeve.

This round's one validated mechanic was "condition-exit > fixed-clock-exit"
(Donchian, on momentum). Feed it back to the L/S mainline, which still exits on a
fixed 3d clock or a percentile-normalize. BUT L/S is MEAN-REVERSION (buys oversold
crowds), so the RIGHT price exit is the OPPOSITE of momentum's Donchian: take
profit when the reversion TARGET is hit, i.e.
  * LONG  (entered on oversold): exit when price rebounds to a prior N-day HIGH.
  * SHORT (entered on overbought): exit when price drops to a prior N-day LOW.
(Momentum did the reverse — exit long on a break of the prior low. Getting the
direction right matters; wrong sign would ride losers.)

Also test "revert to M-day MA" (return to fair value). Compare vs fixed-3d and
normalize, full period incl. 2022 (research/wf.py), per whitelist coin.

    python research/ls_price_exit.py
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
from src.strategy.signal import Params, compute                         # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
PCT_WIN_D = 45


def _ls_base(df):
    return compute(df, Params(lookback_hours=PCT_WIN_D * 24))


def ls_donchian_target(df, k_days):
    """Reversion take-profit: LONG exits at a prior k-day HIGH (rebound achieved),
    SHORT exits at a prior k-day LOW. Opposite sign to momentum's Donchian."""
    sig = _ls_base(df)
    hi = df["close"].rolling(k_days * 24, min_periods=k_days * 24).max().shift(1)
    lo = df["close"].rolling(k_days * 24, min_periods=k_days * 24).min().shift(1)
    # long: exit when price rebounds ABOVE the prior high; short: falls BELOW prior low
    sig["exit_long"] = (df["close"] >= hi).fillna(False)
    sig["exit_short"] = (df["close"] <= lo).fillna(False)
    return sig


def ls_ma_revert(df, ma_days):
    """Exit when price reverts back through its M-day MA (returned to fair value)."""
    sig = _ls_base(df)
    ma = df["close"].rolling(ma_days * 24, min_periods=ma_days * 24).mean()
    # long entered below value -> exit when price climbs back >= MA; short mirror
    sig["exit_long"] = (df["close"] >= ma).fillna(False)
    sig["exit_short"] = (df["close"] <= ma).fillna(False)
    return sig


def main():
    p45 = Params(lookback_hours=PCT_WIN_D * 24)
    cap = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=10 * 24)   # 10d max cap for condition exits
    for sym in COINS:
        df = build_hourly(sym)
        print(f"\n===== {sym} =====")
        # references
        c_time = evaluate(df, _ls_base, dict(params=p45, exit_mode="time"))
        print(fmt(c_time, "fixed 3d (ref)"))
        c_norm = evaluate(df, _ls_base,
                          dict(params=Params(lookback_hours=PCT_WIN_D * 24, hold_hours=5 * 24),
                               exit_mode="normalize", neutral_lo=0.40, neutral_hi=0.60, min_hold_hours=24))
        print(fmt(c_norm, "normalize (ref)"))
        # price-condition exits
        for k in [3, 5, 7]:
            c = evaluate(df, lambda d, k=k: ls_donchian_target(d, k),
                         dict(params=cap, exit_mode="signal", min_hold_hours=24))
            print(fmt(c, f"rebound-to-{k}d-high"))
        for m in [7, 14]:
            c = evaluate(df, lambda d, m=m: ls_ma_revert(d, m),
                         dict(params=cap, exit_mode="signal", min_hold_hours=24))
            print(fmt(c, f"revert-to-{m}d-MA"))


if __name__ == "__main__":
    main()
