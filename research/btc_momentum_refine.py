"""
Experiment #11b — BTC momentum REFINEMENT (hold-period + exit tuning).

The first pass (btc_momentum.py) held momentum for 3 DAYS — but that's the L/S
reversion strategy's hold. Momentum wants to RIDE the trend, so a 3d time-exit
likely cuts winners short. The two best constructions plateaued ~1.0 (tsmom_extreme
N30/45, price_zscore_follow N45). Refine the two levers most likely to push past
the 1.2 bar:
  * hold-period sweep {3,5,7,10,14 d} with a fixed time exit.
  * trend-following exit ("normalize"): ride until the momentum PERCENTILE falls
    back to neutral (0.4-0.6), with a max-hold cap — reuses the engine exit_mode,
    but we now feed pct = the momentum percentile so "reversion done" == "trend faded".

Look for a robust PLATEAU > 1.2 across hold periods, not a lone spike. Walk-forward
OOS, report corr-to-L/S (orthogonality) + MaxDD + trades (>100).

    python research/btc_momentum_refine.py
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
from src.strategy.signal import (FLAT, LONG, SHORT, Params, compute,    # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

SYM = "BTCUSDT"
PCT_WIN_D = 45
HOLDS_D = [3, 5, 7, 10, 14]
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"

# (construction, N_days) pairs to refine — the strongest from the first pass.
CANDIDATES = [("tsmom_extreme", 30), ("tsmom_extreme", 45), ("price_zscore_follow", 45)]


def momentum_pct(df, cons, N):
    """The momentum percentile series that drives entries AND the normalize exit."""
    if cons == "tsmom_extreme":
        val = df["close"].pct_change(N * 24)
    elif cons == "price_zscore_follow":
        x = df["close"]
        val = (x - x.rolling(N * 24, min_periods=N * 24).mean()) / \
              x.rolling(N * 24, min_periods=N * 24).std().replace(0.0, np.nan)
    else:
        raise ValueError(cons)
    return percentile_rank(val, PCT_WIN_D * 24)


def build_frame(df, cons, N, p):
    out = df[["open", "high", "low", "close"]].copy()
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    pct = momentum_pct(df, cons, N)
    out["pct"] = pct                                     # real momentum pct (normalize exit uses it)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG                                # strong up-momentum -> follow
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


def walk_forward(df, sigfull, cfg_kwargs):
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades = [], 0
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        cfg = BacktestConfig(start=str(train_end.date()), end=str(test_end.date()), **cfg_kwargs)
        res = run(df, cfg, signals=sigfull)
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    eqpath = BASE + allret.cumsum() * BASE
    maxdd = float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")
    return allret, n_trades, maxdd


def ls_ref(df, p):
    sigfull = compute(df, p)
    return walk_forward(df, sigfull, dict(params=p, exit_mode="time"))[0]


def main():
    p = Params(lookback_hours=PCT_WIN_D * 24)
    df = build_hourly(SYM)
    ls_ret = ls_ref(df, p)
    print(f"BTC L/S sleeve OOS Sharpe (reference only, NOT the bar): {_sharpe(ls_ret):.2f}")
    print(f"BAR = standalone OOS Sharpe > 1.2\n")

    out = {}
    for cons, N in CANDIDATES:
        pN = Params(lookback_hours=PCT_WIN_D * 24)
        sigfull = build_frame(df, cons, N, pN)
        label = f"{cons} N={N}"
        print(f"=== {label} ===")
        print(f"  {'exit':<16} {'OOSsharpe':>9} {'corrLS':>7} {'MaxDD%':>8} {'trades':>6}  flag")
        rows = []
        # (a) time-exit hold sweep
        for h in HOLDS_D:
            pN2 = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=h * 24)
            dret, ntr, maxdd = walk_forward(df, sigfull,
                                            dict(params=pN2, exit_mode="time"))
            sh = _sharpe(dret)
            common = dret.dropna().index.intersection(ls_ret.dropna().index)
            corr = float(np.corrcoef(dret.loc[common], ls_ret.loc[common])[0, 1]) if len(common) > 10 else float("nan")
            good = (not np.isnan(sh)) and sh > 1.2 and ntr >= 100
            print(f"  {'hold '+str(h)+'d':<16} {sh:>9.2f} {corr:>7.2f} {maxdd*100:>8.1f} {ntr:>6}  {'>1.2 ✓' if good else ''}")
            rows.append({"exit": f"time_{h}d", "oos_sharpe": round(sh, 2), "corr_to_ls": round(corr, 2),
                         "maxdd_pct": round(maxdd * 100, 1), "trades": ntr, "good": good})
        # (b) trend-following normalize exit (ride until momentum fades), 14d cap
        pN3 = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=14 * 24)
        dret, ntr, maxdd = walk_forward(df, sigfull,
                                        dict(params=pN3, exit_mode="normalize",
                                             neutral_lo=0.40, neutral_hi=0.60, min_hold_hours=48))
        sh = _sharpe(dret)
        common = dret.dropna().index.intersection(ls_ret.dropna().index)
        corr = float(np.corrcoef(dret.loc[common], ls_ret.loc[common])[0, 1]) if len(common) > 10 else float("nan")
        good = (not np.isnan(sh)) and sh > 1.2 and ntr >= 100
        print(f"  {'trend-follow':<16} {sh:>9.2f} {corr:>7.2f} {maxdd*100:>8.1f} {ntr:>6}  {'>1.2 ✓' if good else ''}")
        rows.append({"exit": "normalize_14dcap", "oos_sharpe": round(sh, 2), "corr_to_ls": round(corr, 2),
                     "maxdd_pct": round(maxdd * 100, 1), "trades": ntr, "good": good})
        out[label] = rows
        print()

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "btc_momentum_refine.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"saved -> {RESULTS/'btc_momentum_refine.json'}")


if __name__ == "__main__":
    main()
