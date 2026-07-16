"""
Parameter & timeframe sensitivity on BTC. Answers: which knobs move Sharpe, is
there a stable "parameter plateau", does a longer timeframe help, and what's the
single best config found. Every config reports the FULL metric set (Sharpe, total,
CAGR, MaxDD, trade count, trade frequency, win rate, profit factor, worst MAE) so
the dashboard can show trade frequency / drawdown / return side-by-side.

    python research/param_sweep.py
"""
from __future__ import annotations

import json
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
    return {
        "sharpe": round(s["sharpe"], 2),
        "total_pct": round(s["total_return"] * 100),
        "cagr_pct": round(s["cagr"] * 100),
        "maxdd_pct": round(s["max_drawdown"] * 100, 1),
        "trades": s["num_trades"],
        "days_per_trade": round(s["years"] * 365 / s["num_trades"], 1),
        "win_pct": round(s["win_rate"] * 100, 1),
        "pf": round(s["profit_factor"], 2),
        "worst_mae_pct": round(res.trades["mae"].min() * 100, 1),
    }


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

    allrows["lookback"] = section("Lookback (days)", [
        {"lookback_days": lb, **(evalp(df, mk(lookback_days=lb)) or {})}
        for lb in [30, 45, 60, 90, 120, 150, 180]])

    allrows["threshold"] = section("Entry thresholds (percentile)", [
        {"cutoff": f"{int(u*100)}/{int(l*100)}", **(evalp(df, mk(upper=u, lower=l)) or {})}
        for u, l in [(0.80, 0.20), (0.85, 0.15), (0.90, 0.10), (0.95, 0.05), (0.975, 0.025)]])

    allrows["hold"] = section("Hold period (days)", [
        {"hold_days": h, **(evalp(df, mk(hold_days=h)) or {})}
        for h in [1, 2, 3, 4, 5, 7, 10]])

    rows = [{"vol_target": "fixed 1x", **(evalp(df, mk(smin=1.0, smax=1.0)) or {})}]
    for vt in [0.015, 0.02, 0.025, 0.03, 0.04]:
        rows.append({"vol_target": vt, **(evalp(df, mk(vol_target=vt)) or {})})
    allrows["vol"] = section("Vol targeting", rows)

    # lookback x hold grid (parameter plateau)
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

    tf_rows = []
    for hrs, bpd in [(1, 24), (2, 12), (4, 6)]:
        d = df if hrs == 1 else resample(df, hrs)
        r = evalp(d, mk(bpd=bpd))
        tf_rows.append({"timeframe": f"{hrs}h", **(r or {})})
    allrows["timeframe"] = section("Timeframe (Sharpe comparable; returns approx.)", tf_rows)

    # ---- find the single best in-sample config (lookback x hold x threshold) ----
    baseline = evalp(df, mk())
    best, best_cfg = baseline, dict(BASE)
    for lb in [45, 60, 90]:
        for h in [2, 3, 4]:
            for u, l in [(0.90, 0.10), (0.925, 0.075), (0.95, 0.05)]:
                r = evalp(df, mk(lookback_days=lb, hold_days=h, upper=u, lower=l))
                if r and r["sharpe"] > best["sharpe"]:
                    best, best_cfg = r, dict(BASE, lookback_days=lb, hold_days=h, upper=u, lower=l)
    print("\n===== Best in-sample config found =====")
    print(f"baseline: {baseline}")
    print(f"best cfg: {best_cfg}")
    print(f"best    : {best}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "param_sweep.json").write_text(json.dumps(
        {**allrows, "grid": grid, "baseline": baseline,
         "best": {"config": best_cfg, "metrics": best}}, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'param_sweep.json'}")


if __name__ == "__main__":
    main()
