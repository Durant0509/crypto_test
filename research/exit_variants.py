"""
Exit redesign — the fixed 3-day time exit is the strategy's weakest link. Test
"exit on normalization": close when the L/S percentile returns to the neutral band
(reversion complete) instead of waiting a fixed 3 days, freeing capital early.

All variants use the validated 45d lookback, 90/10 entry, inverse-vol sizing, no
stop. Only the EXIT changes:
  * baseline    — fixed 3-day time exit (current)
  * norm N / band — exit when pct back in neutral band, with N-day MAX cap
    (early-exit frees capital, so a longer cap is affordable)

Compared vs baseline; a genuine winner then gets walk-forward validated (--wf).

    python research/exit_variants.py
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
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import Params                       # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
LOOKBACK_D = 45
RESULTS = ROOT / "results"

# (name, exit_mode, cap_days, neutral_lo, neutral_hi)
VARIANTS = [
    ("baseline 3d",        "time",      3, 0.40, 0.60),
    ("norm .40-.60 cap3d", "normalize", 3, 0.40, 0.60),
    ("norm .40-.60 cap5d", "normalize", 5, 0.40, 0.60),
    ("norm .40-.60 cap7d", "normalize", 7, 0.40, 0.60),
    ("norm .45-.55 cap5d", "normalize", 5, 0.45, 0.55),
    ("norm .30-.70 cap5d", "normalize", 5, 0.30, 0.70),
]


def evalv(df, exit_mode, cap_days, nlo, nhi, start="2022-01-01", end="2026-06-30"):
    p = Params(lookback_hours=LOOKBACK_D * 24, hold_hours=cap_days * 24)
    cfg = BacktestConfig(start=start, end=end, params=p,
                         exit_mode=exit_mode, neutral_lo=nlo, neutral_hi=nhi)
    res = run(df, cfg)
    if len(res.trades) < 20:
        return None
    s = summary(res, df["close"])
    return {"sharpe": round(s["sharpe"], 2), "total_pct": round(s["total_return"] * 100),
            "maxdd_pct": round(s["max_drawdown"] * 100, 1), "trades": s["num_trades"],
            "days_per_trade": round(s["years"] * 365 / s["num_trades"], 1),
            "avg_hold_h": round(float(res.trades["hold_hours"].mean()), 1),
            "win_pct": round(s["win_rate"] * 100, 1), "pf": round(s["profit_factor"], 2),
            "worst_mae_pct": round(res.trades["mae"].min() * 100, 1)}


def walk_forward(df, exit_mode, cap_days, nlo, nhi):
    """OOS: fixed config (no re-optimization), concatenate 90d test windows."""
    t0, last = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2026-06-30", tz="UTC")
    rets = []
    train_start = t0
    while True:
        test_start = train_start + pd.Timedelta(days=365)
        test_end = test_start + pd.Timedelta(days=90)
        if test_end > last:
            break
        p = Params(lookback_hours=LOOKBACK_D * 24, hold_hours=cap_days * 24)
        cfg = BacktestConfig(start=str(test_start.date()), end=str(test_end.date()),
                             params=p, exit_mode=exit_mode, neutral_lo=nlo, neutral_hi=nhi)
        res = run(df, cfg)
        daily = res.equity.resample("1D").last().dropna()
        if len(daily) > 1:
            rets.append(daily.diff().fillna(daily.iloc[0] - 1000.0) / 1000.0)
        train_start += pd.Timedelta(days=90)
    r = pd.concat(rets).dropna() if rets else pd.Series(dtype=float)
    sd = r.std()
    return {"oos_sharpe": round(float(r.mean() / sd * np.sqrt(365)), 2) if sd else None,
            "oos_total_pct": round(float(r.sum()) * 100)}


def main():
    out = {}
    for sym in COINS:
        df = build_hourly(sym)
        rows = {}
        for name, mode, cap, lo, hi in VARIANTS:
            rows[name] = evalv(df, mode, cap, lo, hi)
        out[sym] = rows
        print(f"\n===== {sym}  (lookback {LOOKBACK_D}d) =====")
        print(f"{'variant':<22}{'sharpe':>7}{'total%':>8}{'maxDD%':>8}{'trades':>7}"
              f"{'avgHold_h':>10}{'win%':>7}{'MAE%':>8}")
        for name, *_ in VARIANTS:
            r = rows[name]
            if not r: print(f"{name:<22}  (too few)"); continue
            print(f"{name:<22}{r['sharpe']:>7}{r['total_pct']:>8}{r['maxdd_pct']:>8}"
                  f"{r['trades']:>7}{r['avg_hold_h']:>10}{r['win_pct']:>7}{r['worst_mae_pct']:>8}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "exit_variants.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'exit_variants.json'}")


if __name__ == "__main__":
    main()
