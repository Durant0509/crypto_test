"""
Shared walk-forward harness — the FIXED methodology (2026-07).

THE BUG this fixes: the old per-script walk-forward used a 365d train / 90d test
roll starting 2022-01-01, so the first test window began 2023-01-01. All of 2022
(crypto bear: LUNA, FTX) was ALWAYS warmup, NEVER in any test window. The
concatenated OOS curve therefore started in 2023 and silently skipped the single
worst regime. This SYSTEMATICALLY INFLATED any strategy that only works in certain
regimes — momentum showed OOS 1.22 but 0.74 over the full period incl. 2022.

THE FIX: every evaluation reports BOTH, side by side:
  * oos_2023   — concatenated walk-forward test windows (365/90 roll). Genuinely
                 out-of-sample for param selection, but starts 2023 (2022 = warmup).
  * full_2022  — a single fixed-config backtest over the WHOLE period incl. 2022.
                 Not param-selected, but exposes regime dependence.
The GAP (oos_2023 - full_2022) is a regime-dependence diagnostic: small gap =
robust across regimes; large gap = the OOS number leans on having skipped 2022.

Usage: pass a `sig_builder(df) -> signal frame` and cfg kwargs; get a scorecard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run          # noqa: E402

BASE = 1000.0
FULL_START, END = "2022-01-01", "2026-06-30"
TRAIN_D, TEST_D, STEP_D = 365, 90, 90


def _daily_ret(eq):
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def _maxdd_from_ret(dret):
    eqpath = BASE + dret.cumsum() * BASE
    return float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")


def _maxdd_eq(eq):
    return float((eq / eq.cummax() - 1.0).min())


def evaluate(df, sig_builder, cfg_kwargs, *, full_start=FULL_START, end=END,
             ref_ret=None):
    """Return a scorecard dict with BOTH oos_2023 and full_2022 views.

    df          : hourly (or resampled) OHLC(+factor) frame.
    sig_builder : df -> signal frame (open/high/low/close/target/size[/pct/exit_*]).
    cfg_kwargs  : passed to BacktestConfig (params, exit_mode, neutral_*, min_hold_hours,
                  catastrophic_stop, ...). start/end are set by this harness.
    ref_ret     : optional daily-return Series of another sleeve for corr (orthogonality).
    """
    sigfull = sig_builder(df)

    # ---- full period incl. 2022 (single fixed-config backtest) --------------
    res_full = run(df, BacktestConfig(start=full_start, end=end, **cfg_kwargs),
                   signals=sigfull)
    full_ret = _daily_ret(res_full.equity)
    full = {
        "sharpe": round(_sharpe(full_ret), 2),
        "total_pct": round(float(res_full.equity.iloc[-1] / BASE - 1.0) * 100, 1),
        "maxdd_pct": round(_maxdd_eq(res_full.equity) * 100, 1),
        "trades": int(len(res_full.trades)),
    }
    # per-year strategy return (fixed notional)
    eq = res_full.equity
    yearly = {}
    for yr, grp in eq.groupby(eq.index.year):
        prev = eq[eq.index.year < yr]
        ref = prev.iloc[-1] if len(prev) else BASE
        yearly[int(yr)] = round(float((grp.iloc[-1] - ref) / BASE) * 100, 1)
    full["yearly_pct"] = yearly

    # ---- walk-forward OOS (starts 2023; 2022 warmup) ------------------------
    t0, last = pd.Timestamp(full_start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    oos_ret, n_trades, win_tot = [], 0, []
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        res = run(df, BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                                     **cfg_kwargs), signals=sigfull)
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        win_tot.append(float(res.equity.iloc[-1] / BASE - 1.0))
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    npos = sum(1 for t in win_tot if t > 0)
    oos = {
        "sharpe": round(_sharpe(allret), 2),
        "total_pct": round(float(allret.sum()) * 100, 1),
        "maxdd_pct": round(_maxdd_from_ret(allret) * 100, 1),
        "trades": n_trades,
        "windows_positive": f"{npos}/{len(win_tot)}",
        "test_start": "2023-01-01",
    }

    gap = (round(oos["sharpe"] - full["sharpe"], 2)
           if oos["sharpe"] == oos["sharpe"] and full["sharpe"] == full["sharpe"] else None)

    card = {"oos_2023": oos, "full_2022": full, "gap_oos_minus_full": gap}

    if ref_ret is not None:
        common = full_ret.dropna().index.intersection(ref_ret.dropna().index)
        card["corr_to_ref"] = (round(float(np.corrcoef(full_ret.loc[common],
                                                        ref_ret.loc[common])[0, 1]), 2)
                               if len(common) > 10 else None)
    card["_full_daily_ret"] = full_ret          # for chaining as a ref sleeve
    return card


def fmt(card, label=""):
    o, f = card["oos_2023"], card["full_2022"]
    g = card["gap_oos_minus_full"]
    flag = ""
    if g is not None and g >= 0.4:
        flag = "  ⚠ regime-dependent (OOS skips 2022)"
    return (f"{label:<24} OOS23 {o['sharpe']:>5} (DD {o['maxdd_pct']:>6}%) | "
            f"FULL22 {f['sharpe']:>5} (DD {f['maxdd_pct']:>6}%) | gap {g}{flag}")
