"""
Experiment #12b — condition-based EXITS for BTC momentum (not a fixed hold).

Durant's point: a fixed N-day hold is arbitrary — the bumpy hold surface
(6-7d good, 8-9d bad, 10d good) is likely an artifact of exiting on the clock
regardless of whether the trend is still alive. Momentum should RIDE until the
trend actually breaks. Test event/threshold exits vs the fixed-7d reference,
same entry (tsmom_extreme N=30). Question: does a condition exit give a HIGHER
and/or MORE STABLE Sharpe than the fixed hold?

Exits (direction-aware exit_long/exit_short, causal: condition at t-1 -> exit t open):
  * time_7d      : fixed 7-day hold (reference — the current best config)
  * ema_cross_A/B: exit long when fast EMA < slow EMA (death cross); short opposite
  * price_vs_maN : exit long when close < N-day MA; short when close > N-day MA
  * donchian_k   : exit long when close < prior k-day low (trailing); short opposite
  * mom_fade     : exit when 30d-return percentile returns to neutral [.4,.6] (normalize)

All use a 30-day MAX-HOLD cap so a dead trade can't linger forever. Walk-forward OOS.

    python research/btc_momentum_exits.py
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
N_DAYS = 30
PCT_WIN_D = 45
CAP_D = 30                                            # max-hold safety cap (hours below)
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


def base_frame(df, p):
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


def with_ema_cross(frame, df, fast_d, slow_d):
    f = df["close"].ewm(span=fast_d * 24, adjust=False).mean()
    s = df["close"].ewm(span=slow_d * 24, adjust=False).mean()
    out = frame.copy()
    out["exit_long"] = (f < s).reindex(out.index).fillna(False)
    out["exit_short"] = (f > s).reindex(out.index).fillna(False)
    return out


def with_price_vs_ma(frame, df, ma_d):
    ma = df["close"].rolling(ma_d * 24, min_periods=ma_d * 24).mean()
    out = frame.copy()
    out["exit_long"] = (df["close"] < ma).reindex(out.index).fillna(False)
    out["exit_short"] = (df["close"] > ma).reindex(out.index).fillna(False)
    return out


def with_donchian(frame, df, k_d):
    lo = df["close"].rolling(k_d * 24, min_periods=k_d * 24).min().shift(1)
    hi = df["close"].rolling(k_d * 24, min_periods=k_d * 24).max().shift(1)
    out = frame.copy()
    out["exit_long"] = (df["close"] < lo).reindex(out.index).fillna(False)
    out["exit_short"] = (df["close"] > hi).reindex(out.index).fillna(False)
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
    oos_ret, n_trades, win_tot = [], 0, []
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
        win_tot.append(float(res.equity.iloc[-1] / BASE - 1.0))
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    eqpath = BASE + allret.cumsum() * BASE
    maxdd = float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")
    npos = sum(1 for t in win_tot if t > 0)
    return allret, n_trades, maxdd, npos, len(win_tot)


def main():
    p = Params(lookback_hours=PCT_WIN_D * 24)
    df = build_hourly(SYM)
    frame = base_frame(df, p)

    # reference L/S sleeve for corr
    ls_ret, *_ = walk_forward(df, compute(df, p), dict(params=p, exit_mode="time"))

    cap = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=CAP_D * 24)
    p7 = Params(lookback_hours=PCT_WIN_D * 24, hold_hours=7 * 24)

    variants = {
        "time_7d (ref)": (frame, dict(params=p7, exit_mode="time")),
        "mom_fade (normalize)": (frame, dict(params=cap, exit_mode="normalize",
                                             neutral_lo=0.40, neutral_hi=0.60, min_hold_hours=24)),
        "ema_cross_3/7": (with_ema_cross(frame, df, 3, 7), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
        "ema_cross_5/10": (with_ema_cross(frame, df, 5, 10), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
        "price_vs_ma_7": (with_price_vs_ma(frame, df, 7), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
        "price_vs_ma_14": (with_price_vs_ma(frame, df, 14), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
        "donchian_5": (with_donchian(frame, df, 5), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
        "donchian_7": (with_donchian(frame, df, 7), dict(params=cap, exit_mode="signal", min_hold_hours=24)),
    }

    print(f"BTC momentum (tsmom_extreme N=30) — EXIT comparison")
    print(f"BAR = OOS Sharpe > 1.2 (ref fixed-7d was 1.30 but bumpy vs hold)\n")
    print(f"  {'exit':<22} {'OOSsharpe':>9} {'corrLS':>7} {'MaxDD%':>8} {'trades':>6} {'winW':>6}")
    out = {}
    for name, (sig, kw) in variants.items():
        dret, ntr, maxdd, npos, nwin = walk_forward(df, sig, kw)
        sh = _sharpe(dret)
        common = dret.dropna().index.intersection(ls_ret.dropna().index)
        corr = float(np.corrcoef(dret.loc[common], ls_ret.loc[common])[0, 1]) if len(common) > 10 else float("nan")
        flag = ">1.2 ✓" if sh > 1.2 else ""
        print(f"  {name:<22} {sh:>9.2f} {corr:>7.2f} {maxdd*100:>8.1f} {ntr:>6} {f'{npos}/{nwin}':>6}  {flag}")
        out[name] = {"oos_sharpe": round(sh, 2), "corr_to_ls": round(corr, 2),
                     "maxdd_pct": round(maxdd * 100, 1), "trades": ntr, "windows_positive": f"{npos}/{nwin}"}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "btc_momentum_exits.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'btc_momentum_exits.json'}")


if __name__ == "__main__":
    main()
