"""
Experiment #22 — anchored expanding-window walk-forward = an OVERFIT PROBE.

Durant's concern: don't overfit; make validation realistic; don't do unnatural
things to pump Sharpe. This is the most realistic simulation of "if I had only the
data up to time T, what lookback would I have PICKED, and how did it then perform
on the *future* I hadn't seen?"

Design (realistic, not leave-one-year-out — that trains on the future):
  * ANCHORED expanding train window: train start fixed at 2022-01-01, train END
    grows. You never throw away old data (matches live).
  * INITIAL train = 270d (shorter than the old 365d) so the first TEST window
    starts ~2022-10 — this pulls the 2022 FTX crash INTO a test window instead of
    burying it in warmup forever.
  * On each train window, RE-SELECT the best lookback from {30,45,60,90}. Apply it
    to the next unseen 90d test window. Concatenate test windows -> OOS curve.
  * Also run FIXED 45d and FIXED 90d on the SAME test windows.

THE OVERFIT VERDICT is NOT "is Sharpe high" — it's the GAP between adaptive
re-selection and fixed 45d:
  * adaptive ≈ fixed 45d  -> 45d is robust, NOT a lucky post-hoc pick.
  * fixed 45d >> adaptive  -> 45d only looks good with hindsight = overfit flag.
  * fixed 45d << adaptive  -> constant param leaves money on the table (rare).
We report both plus which lookback each window actually picked.

    python research/anchored_wf.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from src.backtest.engine import BacktestConfig, run                     # noqa: E402
from src.strategy.signal import Params, compute                         # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
LOOKBACKS = [30, 45, 60, 90]
INIT_TRAIN_D, TEST_D, STEP_D = 270, 90, 90
ANCHOR, END = "2022-01-01", "2026-06-30"
BASE = 1000.0


def _dret(eq):
    d = eq.resample("1D").last().dropna()
    return d.diff().fillna(d.iloc[0] - BASE) / BASE if len(d) >= 2 else pd.Series(dtype=float)


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def _win(df, start, end, lb):
    cfg = BacktestConfig(start=str(start.date()), end=str(end.date()),
                         params=Params(lookback_hours=lb * 24))
    res = run(df, cfg)
    return _dret(res.equity), int(len(res.trades))


def anchored(sym):
    df = build_hourly(sym)
    anchor = pd.Timestamp(ANCHOR, tz="UTC")
    last = pd.Timestamp(END, tz="UTC")

    adaptive, fixed45, fixed90 = [], [], []
    picks, win_rows = [], []
    train_end = anchor + pd.Timedelta(days=INIT_TRAIN_D)   # expanding: only this moves
    while True:
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        # re-select best lookback on the ANCHORED train window (2022-01 .. train_end)
        best_lb, best_sh = 90, -1e9
        for lb in LOOKBACKS:
            dret, nt = _win(df, anchor, train_end, lb)
            sh = _sharpe(dret)
            if nt >= 10 and not np.isnan(sh) and sh > best_sh:
                best_sh, best_lb = sh, lb
        d_ad, nt_ad = _win(df, train_end, test_end, best_lb)
        d_45, _ = _win(df, train_end, test_end, 45)
        d_90, _ = _win(df, train_end, test_end, 90)
        adaptive.append(d_ad); fixed45.append(d_45); fixed90.append(d_90)
        picks.append(best_lb)
        win_rows.append({"test": f"{train_end.date()}→{test_end.date()}",
                         "pick": best_lb, "sharpe": round(_sharpe(d_ad), 2),
                         "ret_pct": round(float(d_ad.sum()) * 100, 1)})
        train_end = train_end + pd.Timedelta(days=STEP_D)

    def cat(x): return pd.concat(x) if x else pd.Series(dtype=float)
    ad, f45, f90 = cat(adaptive), cat(fixed45), cat(fixed90)
    return {
        "sym": sym,
        "n_windows": len(win_rows),
        "first_test": win_rows[0]["test"] if win_rows else None,
        "sh_adaptive": round(_sharpe(ad), 2),
        "sh_fixed45": round(_sharpe(f45), 2),
        "sh_fixed90": round(_sharpe(f90), 2),
        "picks": dict(Counter(picks)),
        "windows": win_rows,
    }


def main():
    print("=" * 92)
    print("Anchored expanding-window walk-forward (OVERFIT PROBE) — L/S, first test incl. 2022 crash")
    print("=" * 92)
    print("verdict rule: adaptive≈fixed45 -> 45d robust (not overfit); fixed45>>adaptive -> hindsight/overfit\n")
    for sym in COINS:
        r = anchored(sym)
        gap = round(r["sh_fixed45"] - r["sh_adaptive"], 2)
        flag = ("45d≈adaptive OK" if abs(gap) <= 0.2
                else ("⚠ fixed45 hindsight-better (overfit?)" if gap > 0.2
                      else "adaptive better (constant leaves money)"))
        print(f"{r['sym']}: {r['n_windows']} windows from {r['first_test']}")
        print(f"   adaptive re-select {r['sh_adaptive']}  |  fixed45 {r['sh_fixed45']}  |  "
              f"fixed90 {r['sh_fixed90']}  |  gap(45-ad) {gap}  -> {flag}")
        print(f"   lookbacks picked on train: {r['picks']}")
        # show the 2022 test windows explicitly (the ones the old method hid)
        early = [w for w in r["windows"] if w["test"].startswith("2022")]
        if early:
            print(f"   2022 test windows (previously hidden as warmup):")
            for w in early:
                print(f"      {w['test']}  pick {w['pick']}d  Sharpe {w['sharpe']}  ret {w['ret_pct']}%")
        print()


if __name__ == "__main__":
    main()
