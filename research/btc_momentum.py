"""
Experiment #11 — BTC price-MOMENTUM factor (feasibility + param tuning).

BTC-FIRST (Durant 2026-07). The discovery scan showed fading BTC price extremes
loses -1.67 OOS => a real TREND effect exists on BTC, but crude MACD-follow only
scored 0.50. So the momentum EDGE is there; the crude CONSTRUCTION hid it. Test
proper momentum constructions, tune the lookback, and see if BTC momentum clears
the standalone bar (OOS Sharpe > 1.2). Also measure corr to the L/S sleeve — if a
BTC momentum sleeve is orthogonal, the two together beat either alone.

Constructions (all: 3d hold, inverse-vol sizing, real costs, walk-forward OOS):
  * tsmom_sign      : always-in, target = sign(trailing N-day return)  [classic TSMOM]
  * tsmom_extreme   : follow the 45d-percentile extreme of N-day return (strong trend)
  * price_zscore_follow : follow the 45d-percentile extreme of price z-score(N) [mirror of -1.67]
  * donchian        : long above prior N-day high, short below prior N-day low  [breakout]

    python research/btc_momentum.py
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
LOOKBACKS_D = [7, 14, 21, 30, 45]            # momentum formation window (days)
PCT_WIN_D = 45                                # percentile window for extreme constructions
HOLD_H = 72
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


def _base_cols(df, p):
    out = df[["open", "high", "low", "close"]].copy()
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    out["pct"] = 0.5                                     # dummy (time-exit doesn't use it)
    return out


def sig_tsmom_sign(df, N, p):
    out = _base_cols(df, p)
    mom = df["close"].pct_change(N * 24)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[mom > 0] = LONG
    t[mom < 0] = SHORT
    t[mom.isna()] = FLAT
    out["target"] = t
    return out


def sig_tsmom_extreme(df, N, p):
    out = _base_cols(df, p)
    mom = df["close"].pct_change(N * 24)
    pct = percentile_rank(mom, PCT_WIN_D * 24)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG                                # strong up-momentum -> follow
    t[pct <= 0.10] = SHORT
    t[pct.isna()] = FLAT
    out["target"] = t
    return out


def sig_price_zscore_follow(df, N, p):
    out = _base_cols(df, p)
    x = df["close"]
    z = (x - x.rolling(N * 24, min_periods=N * 24).mean()) / \
        x.rolling(N * 24, min_periods=N * 24).std().replace(0.0, np.nan)
    pct = percentile_rank(z, PCT_WIN_D * 24)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG                                # price stretched up -> follow (momentum)
    t[pct <= 0.10] = SHORT
    t[pct.isna()] = FLAT
    out["target"] = t
    return out


def sig_donchian(df, N, p):
    out = _base_cols(df, p)
    hi = df["close"].rolling(N * 24, min_periods=N * 24).max().shift(1)
    lo = df["close"].rolling(N * 24, min_periods=N * 24).min().shift(1)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[df["close"] > hi] = LONG
    t[df["close"] < lo] = SHORT
    out["target"] = t
    return out


CONSTRUCTIONS = {
    "tsmom_sign": sig_tsmom_sign,
    "tsmom_extreme": sig_tsmom_extreme,
    "price_zscore_follow": sig_price_zscore_follow,
    "donchian": sig_donchian,
}


def _daily_ret(eq):
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def walk_forward(df, sig_builder, N, p):
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    sigfull = sig_builder(df, N, p)
    oos_ret, n_trades = [], 0
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        res = run(df, BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                                     params=p), signals=sigfull)
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    eqpath = BASE + allret.cumsum() * BASE
    maxdd = float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")
    return allret, n_trades, maxdd


def ls_baseline_ret(df, p):
    """L/S fade sleeve OOS daily returns, for orthogonality corr."""
    from src.strategy.signal import compute
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    sigfull = compute(df, p)
    oos = []
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        res = run(df, BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                                     params=p), signals=sigfull)
        oos.append(_daily_ret(res.equity))
        train_start += pd.Timedelta(days=STEP_D)
    return pd.concat(oos) if oos else pd.Series(dtype=float)


def main():
    p = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=HOLD_H)
    df = build_hourly(SYM)
    ls_ret = ls_baseline_ret(df, p)
    print(f"BTC L/S sleeve OOS Sharpe (reference): {_sharpe(ls_ret):.2f}\n")
    print(f"{'construction':<20} {'N(d)':>4} {'OOSsharpe':>9} {'corrLS':>7} {'MaxDD%':>8} {'trades':>6}  flag")

    out = {}
    for cname, builder in CONSTRUCTIONS.items():
        rows = []
        for N in LOOKBACKS_D:
            dret, ntr, maxdd = walk_forward(df, builder, N, p)
            sh = _sharpe(dret)
            common = dret.dropna().index.intersection(ls_ret.dropna().index)
            corr = (float(np.corrcoef(dret.loc[common], ls_ret.loc[common])[0, 1])
                    if len(common) > 10 else float("nan"))
            good = (not np.isnan(sh)) and sh > 1.2 and ntr >= 100
            flag = "RECORD+SIM" if good else ("orthogonal?" if abs(corr) < 0.3 and sh > 0.8 else "")
            print(f"{cname:<20} {N:>4} {sh:>9.2f} {corr:>7.2f} {maxdd*100:>8.1f} {ntr:>6}  {flag}")
            rows.append({"N_days": N, "oos_sharpe": round(sh, 2), "corr_to_ls": round(corr, 2),
                         "maxdd_pct": round(maxdd * 100, 1), "trades": ntr, "good": good})
        out[cname] = rows
        print()

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "btc_momentum.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"saved -> {RESULTS/'btc_momentum.json'}")


if __name__ == "__main__":
    main()
