"""
Walk-forward (out-of-sample) validation — the single most important "real vs
curve-fit" test. Run INDEPENDENTLY per coin (no portfolio mixing).

Procedure, rolling across 2022-01-01 .. 2026-06-30:
  * train window = 365d, test window = 90d, step = 90d.
  * On each TRAIN window, pick the lookback (30/45/60/90d) with the best Sharpe.
  * Apply that lookback to the immediately-following TEST window — data the
    selection never saw. Record the test result.
  * Concatenate all test windows -> one out-of-sample (OOS) equity curve & Sharpe.

The percentile signal is causal (trailing window), and the lookback is chosen
only from train-window performance, so the test windows are genuinely unseen.

We also run FIXED 45d and FIXED 90d on the exact same test windows, to answer:
does the in-sample-best 45d actually hold up OOS, or was it overfit?

    python research/walk_forward.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                       # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.strategy.signal import Params                       # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT", "ETHUSDT"]
LOOKBACKS = [30, 45, 60, 90]                 # candidate lookbacks (days)
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


def window_run(df, start, end, lookback_days):
    """Run one window; return (daily_returns Series, sharpe, total, n_trades)."""
    cfg = BacktestConfig(start=str(start.date()), end=str(end.date()),
                         params=Params(lookback_hours=lookback_days * 24))
    res = run(df, cfg)
    eq = res.equity
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float), float("nan"), 0.0, 0
    dret = daily.diff().fillna(daily.iloc[0] - BASE) / BASE
    sd = dret.std()
    sharpe = float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")
    total = float(eq.iloc[-1] / BASE - 1.0)
    return dret, sharpe, total, len(res.trades)


def oos_from_returns(dret: pd.Series) -> dict:
    dret = dret.dropna()
    sd = dret.std()
    sharpe = float(dret.mean() / sd * np.sqrt(365)) if sd else float("nan")
    total = float(dret.sum())                 # fixed-notional: additive
    return {"sharpe": round(sharpe, 2), "total_pct": round(total * 100)}


def walk_forward(sym: str) -> dict:
    df = build_hourly(sym)
    t0 = pd.Timestamp(START, tz="UTC")
    last = pd.Timestamp(END, tz="UTC")

    windows, picks = [], []
    adaptive_ret, fixed45_ret, fixed90_ret = [], [], []
    equity_curve = []          # chained OOS cumulative % (adaptive)
    cum = 0.0

    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break

        # pick best lookback on TRAIN
        best_lb, best_sh = None, -1e9
        for lb in LOOKBACKS:
            _, sh, _, nt = window_run(df, train_start, train_end, lb)
            if nt >= 10 and not np.isnan(sh) and sh > best_sh:
                best_sh, best_lb = sh, lb
        if best_lb is None:
            best_lb = 90       # fallback if train too thin

        # evaluate on TEST (unseen)
        d_ad, sh_ad, tot_ad, nt_ad = window_run(df, train_end, test_end, best_lb)
        d_45, _, _, _ = window_run(df, train_end, test_end, 45)
        d_90, _, _, _ = window_run(df, train_end, test_end, 90)
        adaptive_ret.append(d_ad); fixed45_ret.append(d_45); fixed90_ret.append(d_90)
        picks.append(best_lb)

        for ts, r in d_ad.items():
            cum += float(r) * 100
            equity_curve.append({"d": ts.strftime("%Y-%m-%d"), "eq": round(cum, 2)})

        windows.append({
            "test_start": str(train_end.date()), "test_end": str(test_end.date()),
            "picked_lookback": best_lb, "sharpe": round(sh_ad, 2),
            "total_pct": round(tot_ad * 100, 1), "trades": nt_ad,
            "positive": tot_ad > 0,
        })
        train_start = train_start + pd.Timedelta(days=STEP_D)

    adaptive = pd.concat(adaptive_ret) if adaptive_ret else pd.Series(dtype=float)
    f45 = pd.concat(fixed45_ret) if fixed45_ret else pd.Series(dtype=float)
    f90 = pd.concat(fixed90_ret) if fixed90_ret else pd.Series(dtype=float)
    n_pos = sum(1 for w in windows if w["positive"])

    return {
        "symbol": sym,
        "n_windows": len(windows),
        "windows_positive": n_pos,
        "windows_positive_pct": round(n_pos / len(windows) * 100) if windows else 0,
        "picked_lookbacks": picks,
        "oos_adaptive": oos_from_returns(adaptive),
        "oos_fixed45": oos_from_returns(f45),
        "oos_fixed90": oos_from_returns(f90),
        "windows": windows,
        "equity_curve": equity_curve,
    }


def main():
    out = {}
    for sym in COINS:
        r = walk_forward(sym)
        out[sym] = r
        from collections import Counter
        pk = Counter(r["picked_lookbacks"])
        print(f"\n===== {sym} walk-forward ({r['n_windows']} test windows) =====")
        print(f"  OOS adaptive : Sharpe {r['oos_adaptive']['sharpe']}  total {r['oos_adaptive']['total_pct']}%")
        print(f"  OOS fixed 45d: Sharpe {r['oos_fixed45']['sharpe']}  total {r['oos_fixed45']['total_pct']}%")
        print(f"  OOS fixed 90d: Sharpe {r['oos_fixed90']['sharpe']}  total {r['oos_fixed90']['total_pct']}%")
        print(f"  windows positive: {r['windows_positive']}/{r['n_windows']} ({r['windows_positive_pct']}%)")
        print(f"  lookbacks picked on train: {dict(pk)}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "walk_forward.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'walk_forward.json'}")


if __name__ == "__main__":
    main()
