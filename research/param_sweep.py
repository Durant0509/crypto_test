"""
Parameter & timeframe sensitivity on BTC. Answers: which knobs move Sharpe, is
there a stable "parameter plateau", and does a longer timeframe help?

    python research/param_sweep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import Params                       # noqa: E402

DATA = ROOT / "data" / "hourly.parquet"
RESULTS = ROOT / "results"
BASE = dict(lookback_days=90, upper=0.90, lower=0.10, hold_days=3,
            vol_target=0.025, smin=0.25, smax=3.0)


def mk(bpd=24, **kw):
    """Build Params; durations given in DAYS are converted to bars (bpd=bars/day).
    Default bpd=24 because the base dataset is hourly (24 bars/day)."""
    p = {**BASE, **kw}
    return Params(
        lookback_hours=int(p["lookback_days"] * bpd),
        upper_pct=p["upper"], lower_pct=p["lower"],
        hold_hours=int(p["hold_days"] * bpd),
        vol_window_hours=int(3 * bpd),
        target_daily_vol=p["vol_target"], size_min=p["smin"], size_max=p["smax"],
    )


def evalp(df, params):
    res = run(df, BacktestConfig(start="2022-01-01", end="2026-06-30", params=params))
    if len(res.trades) == 0:
        return None
    s = summary(res, df["close"])
    return {"sharpe": round(s["sharpe"], 2), "total_%": round(s["total_return"] * 100),
            "maxDD_%": round(s["max_drawdown"] * 100, 1), "trades": s["num_trades"],
            "win_%": round(s["win_rate"] * 100, 1)}


def resample(df, hours):
    o = df.resample(f"{hours}h").agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last", "volume": "sum", "lsr": "last"})
    return o.dropna(subset=["close"])


def section(title, rows):
    print(f"\n===== {title} =====")
    print(pd.DataFrame(rows).to_string(index=False))
    return rows


def main():
    df = pd.read_parquet(DATA)
    print(f"BTC hourly {df.index.min().date()}..{df.index.max().date()}  baseline={BASE}")
    allrows = {}

    # 1) lookback sensitivity
    allrows["lookback"] = section("Lookback (days)", [
        {"lookback_days": lb, **(evalp(df, mk(lookback_days=lb)) or {})}
        for lb in [30, 45, 60, 90, 120, 150, 180]])

    # 2) percentile thresholds
    allrows["threshold"] = section("Entry thresholds (percentile)", [
        {"cutoff": f"{int(u*100)}/{int(l*100)}", **(evalp(df, mk(upper=u, lower=l)) or {})}
        for u, l in [(0.80, 0.20), (0.85, 0.15), (0.90, 0.10), (0.95, 0.05), (0.975, 0.025)]])

    # 3) hold period
    allrows["hold"] = section("Hold period (days)", [
        {"hold_days": h, **(evalp(df, mk(hold_days=h)) or {})}
        for h in [1, 2, 3, 4, 5, 7, 10]])

    # 4) vol targeting (incl. fixed 1x = no vol sizing)
    rows = [{"vol_target": "fixed 1x", **(evalp(df, mk(smin=1.0, smax=1.0)) or {})}]
    for vt in [0.015, 0.02, 0.025, 0.03, 0.04]:
        rows.append({"vol_target": vt, **(evalp(df, mk(vol_target=vt)) or {})})
    allrows["vol"] = section("Vol targeting", rows)

    # 5) lookback x hold grid (parameter plateau)
    print("\n===== Sharpe grid: lookback (rows) x hold-days (cols) =====")
    grid = {}
    for lb in [45, 60, 90, 120, 150]:
        grid[lb] = {}
        for h in [2, 3, 4, 5, 7]:
            r = evalp(df, mk(lookback_days=lb, hold_days=h))
            grid[lb][h] = r["sharpe"] if r else float("nan")
    gdf = pd.DataFrame(grid).T
    gdf.index.name = "lookback\\hold"
    print(gdf.to_string())

    # 6) timeframe
    tf_rows = []
    for hrs, bpd in [(1, 24), (2, 12), (4, 6)]:
        d = df if hrs == 1 else resample(df, hrs)
        r = evalp(d, mk(bpd=bpd))
        tf_rows.append({"timeframe": f"{hrs}h", **(r or {})})
    allrows["timeframe"] = section("Timeframe (Sharpe comparable; returns approx.)", tf_rows)

    RESULTS.mkdir(exist_ok=True)
    import json
    (RESULTS / "param_sweep.json").write_text(json.dumps({**allrows, "grid": grid}, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'param_sweep.json'}")


if __name__ == "__main__":
    main()
