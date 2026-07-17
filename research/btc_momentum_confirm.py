"""
Experiment #12 — solidify the BTC momentum plateau (tsmom_extreme N=30).

Refinement found hold 7d=1.30, 10d=1.28 (>1.2 bar). Before trusting it:
  * fill the plateau (holds 6..11d) — is the whole 7-10 region high, or 2 lucky points?
  * per-window positivity — is 1.3 carried by 1-2 windows, or broad across the 14
    walk-forward test windows? (SOP: no single-window dependence.)

    python research/btc_momentum_confirm.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from src.backtest.engine import BacktestConfig, run                     # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params,             # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

SYM = "BTCUSDT"
N_DAYS = 30
PCT_WIN_D = 45
HOLDS_D = [6, 7, 8, 9, 10, 11]
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


def build_frame(df, p):
    out = df[["open", "high", "low", "close"]].copy()
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    mom = df["close"].pct_change(N_DAYS * 24)
    pct = percentile_rank(mom, PCT_WIN_D * 24)
    out["pct"] = pct
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG
    t[pct <= 0.10] = SHORT
    t[pct.isna()] = FLAT
    out["target"] = t
    return out


def _daily_ret(eq):
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def walk_forward(df, sigfull, hold_h):
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades = [], 0
    win_tot = []                                        # per-window total return
    train_start = t0
    p = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=hold_h)
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        res = run(df, BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                                     params=p), signals=sigfull)
        dret = _daily_ret(res.equity)
        oos_ret.append(dret)
        n_trades += len(res.trades)
        win_tot.append(float(res.equity.iloc[-1] / BASE - 1.0))
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    n_pos = sum(1 for t in win_tot if t > 0)
    eqpath = BASE + allret.cumsum() * BASE
    maxdd = float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")
    return _sharpe(allret), n_trades, maxdd, n_pos, len(win_tot), win_tot


def main():
    p = Params(lookback_hours=PCT_WIN_D * 24)
    df = build_hourly(SYM)
    sigfull = build_frame(df, p)
    print(f"BTC tsmom_extreme N={N_DAYS} — plateau + per-window check\n")
    print(f"  {'hold':<6} {'OOSsharpe':>9} {'MaxDD%':>8} {'trades':>6} {'win-windows':>12}")
    out = {}
    for h in HOLDS_D:
        sh, ntr, maxdd, npos, nwin, wt = walk_forward(df, sigfull, h * 24)
        flag = ">1.2 ✓" if sh > 1.2 else ""
        print(f"  {str(h)+'d':<6} {sh:>9.2f} {maxdd*100:>8.1f} {ntr:>6} {f'{npos}/{nwin}':>12}  {flag}")
        out[f"{h}d"] = {"oos_sharpe": round(sh, 2), "maxdd_pct": round(maxdd * 100, 1),
                        "trades": ntr, "windows_positive": f"{npos}/{nwin}",
                        "window_totals_pct": [round(t * 100, 1) for t in wt]}
    # per-window detail for the 7d anchor
    print("\n  per-window total% at hold 7d (check no single-window dependence):")
    print("   ", out["7d"]["window_totals_pct"])
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "btc_momentum_confirm.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'btc_momentum_confirm.json'}")


if __name__ == "__main__":
    main()
