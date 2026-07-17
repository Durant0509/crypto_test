"""
Experiment #20 — L/S + momentum two-sleeve combo (BTC).

THESIS (the session's best remaining idea): the two sleeves earn in OPPOSITE
regimes —
  * L/S contrarian: strong in the 2022 bear (+42.7%), earns from panic/oversold.
  * momentum:        strong in trend years (2023 +148%, 2026 +39%), gets crushed
                     in the 2022 chop (-59%).
If their return time-series are low/negatively correlated, an equal-weight combo
of two INDEPENDENT accounts can have a HIGHER Sharpe than either alone — even
though momentum standalone is a failing 0.74. That would resurrect momentum as a
DIVERSIFIER (not a standalone factor), which the single-factor >1.2 bar can't see.

Method: take each sleeve's full-period (incl. 2022) daily returns from wf.py,
combine as equal-weight (and inverse-vol weight) two-account portfolio, report
combined Sharpe / MaxDD / per-year, and the sleeve-to-sleeve correlation.

    python research/combo_sleeves.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from research.wf import evaluate                                         # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params, compute,    # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

SYM = "BTCUSDT"
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


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def _stats(dret, label):
    dret = dret.dropna()
    eq = 1000.0 + dret.cumsum() * 1000.0
    maxdd = float((eq / eq.cummax() - 1.0).min())
    # per-year total (fixed-notional additive)
    yearly = {int(y): round(float(g.sum()) * 100, 1) for y, g in dret.groupby(dret.index.year)}
    return {"label": label, "sharpe": round(_sharpe(dret), 2),
            "total_pct": round(float(dret.sum()) * 100, 1),
            "maxdd_pct": round(maxdd * 100, 1), "yearly_pct": yearly}


def main():
    df = build_hourly(SYM)
    ls = evaluate(df, ls_builder, dict(params=Params(lookback_hours=PCT_WIN_D * 24), exit_mode="time"))
    mom = evaluate(df, mom_builder, dict(params=Params(hold_hours=30 * 24),
                                         exit_mode="signal", min_hold_hours=24))
    ls_ret = ls["_full_daily_ret"].dropna()
    mom_ret = mom["_full_daily_ret"].dropna()

    # align on common daily index
    idx = ls_ret.index.intersection(mom_ret.index)
    ls_ret, mom_ret = ls_ret.loc[idx], mom_ret.loc[idx]
    corr = float(np.corrcoef(ls_ret, mom_ret)[0, 1])

    # equal-weight: each account gets half the capital -> average the returns
    ew = 0.5 * ls_ret + 0.5 * mom_ret
    # inverse-vol weight (static, from full-period vol) — risk-parity-ish
    v_ls, v_mom = ls_ret.std(), mom_ret.std()
    w_ls = (1 / v_ls) / (1 / v_ls + 1 / v_mom)
    w_mom = 1 - w_ls
    ivw = w_ls * ls_ret + w_mom * mom_ret

    print("=" * 72)
    print("BTC two-sleeve combo — full period incl. 2022")
    print("=" * 72)
    print(f"sleeve-to-sleeve daily-return correlation: {corr:+.2f}\n")
    rows = [_stats(ls_ret, "L/S only"), _stats(mom_ret, "momentum only"),
            _stats(ew, "50/50 equal-weight"),
            _stats(ivw, f"inverse-vol ({w_ls:.0%} L/S / {w_mom:.0%} mom)")]
    for r in rows:
        print(f"  {r['label']:<32} Sharpe {r['sharpe']:>5}  total {r['total_pct']:>7}%  "
              f"MaxDD {r['maxdd_pct']:>7}%")
    print("\n  per-year total return %:")
    print(f"  {'':<32} " + "  ".join(f"{y:>7}" for y in sorted(rows[2]['yearly_pct'])))
    for r in rows:
        cells = "  ".join(f"{r['yearly_pct'].get(y, 0):>7.1f}" for y in sorted(rows[2]['yearly_pct']))
        print(f"  {r['label']:<32} {cells}")

    best = max(rows, key=lambda r: r["sharpe"])
    print(f"\n  >> best Sharpe: {best['label']} @ {best['sharpe']} "
          f"(L/S alone {rows[0]['sharpe']}, mom alone {rows[1]['sharpe']})")


if __name__ == "__main__":
    main()
