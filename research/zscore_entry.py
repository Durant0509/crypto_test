"""
Experiment #3 — Z-score entry vs percentile entry (idea A).

HYPOTHESIS (SOP phase 0):
  Percentile-rank of the L/S ratio is RANK-based: it only knows ordering, is
  bounded [0,1], robust to outliers — but it flattens *how* extreme today is
  (90th pct could be a mild lean or a 3-sigma blowout). A z-score
  (LSR - rolling_mean_45d)/rolling_std_45d is MAGNITUDE-based: it measures
  extremity in sigmas, so it can (a) time entries differently at the tails and
  (b) enable extremity-GRADED sizing (bigger sigma -> bigger position). This is a
  MECHANICS change (same single factor, different lens), the category that has
  raised OOS Sharpe historically. Trade-off: z-score assumes a stable-ish
  distribution; fat tails/regime shifts can distort sigma. Which wins = empirical.

ARMS (all fixed 45d, time exit, same inverse-vol base sizing):
  * percentile : baseline (>=90th short / <=10th long).
  * zscore     : |z| threshold sweep (z>=+Z short / z<=-Z long).
  * z-graded   : zscore entries, size additionally scaled by |z|/Z (clipped).

    python research/zscore_entry.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                        # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.strategy.signal import FLAT, LONG, SHORT, Params, compute  # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT", "ETHUSDT"]
ZTHR = [1.5, 1.75, 2.0, 2.25, 2.5]           # sigma thresholds to sweep
LOOKBACK_D = 45
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


def zscore(lsr: pd.Series, window: int) -> pd.Series:
    m = lsr.rolling(window, min_periods=window).mean()
    sd = lsr.rolling(window, min_periods=window).std()
    return (lsr - m) / sd.replace(0.0, np.nan)


def build_signals(df: pd.DataFrame, params: Params, mode: str, zthr: float) -> pd.DataFrame:
    """mode in {percentile, zscore, zgraded}. Returns a signal frame for the engine."""
    sig = compute(df, params)                # gives pct, dvol, size, target (percentile)
    if mode == "percentile":
        return sig

    z = zscore(df["lsr"], params.lookback_hours).reindex(sig.index)
    target = pd.Series(FLAT, index=sig.index, dtype=int)
    target[z >= zthr] = SHORT                # crowd over-long -> short
    target[z <= -zthr] = LONG                # crowd over-short -> long
    target[z.isna()] = FLAT
    sig = sig.copy()
    sig["target"] = target

    if mode == "zgraded":
        # scale the inverse-vol base size by how many sigmas past the threshold,
        # clipped so a 3-sigma event isn't a 10x bet. g in [1.0, 2.0].
        g = (z.abs() / zthr).clip(lower=1.0, upper=2.0).fillna(1.0)
        sig["size"] = (sig["size"] * g).clip(lower=params.size_min, upper=params.size_max * 1.5)
    return sig


def _daily_ret(eq: pd.Series) -> pd.Series:
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret: pd.Series) -> float:
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def _maxdd(eq: pd.Series) -> float:
    peak = eq.cummax()
    return float((eq / peak - 1.0).min())


def run_full(df, mode, zthr) -> dict:
    params = Params(lookback_hours=LOOKBACK_D * 24)
    sig = build_signals(df, params, mode, zthr)
    res = run(df, BacktestConfig(start=START, end=END, params=params), signals=sig)
    dret = _daily_ret(res.equity)
    return {"mode": mode, "zthr": zthr, "sharpe": round(_sharpe(dret), 2),
            "total_pct": round(float(res.equity.iloc[-1] / BASE - 1.0) * 100, 1),
            "maxdd_pct": round(_maxdd(res.equity) * 100, 1), "trades": int(len(res.trades)),
            "win_rate": round(float(res.trades["win"].mean()) * 100, 1) if len(res.trades) else 0.0}


def walk_forward(df, mode, zthr) -> dict:
    params = Params(lookback_hours=LOOKBACK_D * 24)
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades = [], 0
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        sig = build_signals(df, params, mode, zthr)
        res = run(df, BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                                     params=params), signals=sig)
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    return {"mode": mode, "zthr": zthr, "oos_sharpe": round(_sharpe(allret), 2),
            "oos_total_pct": round(float(allret.sum()) * 100, 1), "trades": n_trades}


def main():
    out = {}
    for sym in COINS:
        df = build_hourly(sym)
        print(f"\n===== {sym} =====")
        base_wf = walk_forward(df, "percentile", 0)
        print(f"  percentile baseline : OOS Sharpe {base_wf['oos_sharpe']}  "
              f"total {base_wf['oos_total_pct']}%  trades {base_wf['trades']}")
        print("  z-score threshold sweep (OOS):")
        z_wf, zg_wf = [], []
        for zt in ZTHR:
            r = walk_forward(df, "zscore", zt)
            rg = walk_forward(df, "zgraded", zt)
            z_wf.append(r); zg_wf.append(rg)
            print(f"    z>={zt}: zscore OOS {r['oos_sharpe']:>5} (tr {r['trades']:>3})   "
                  f"z-graded OOS {rg['oos_sharpe']:>5} (tr {rg['trades']:>3})")
        out[sym] = {"baseline": base_wf, "zscore": z_wf, "zgraded": zg_wf,
                    "full_baseline": run_full(df, "percentile", 0)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "zscore_entry.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'zscore_entry.json'}")


if __name__ == "__main__":
    main()
