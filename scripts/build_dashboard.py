"""
Build the data file for the web dashboard (docs/data.js).

Re-runs the backtest to get the equity series, then writes everything the
dashboard needs as a single inlined JS object so the page works standalone
(file://) and on GitHub Pages with no server / no CORS.

    python scripts/build_dashboard.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run                 # noqa: E402
from src.backtest.metrics import drawdown_series, summary            # noqa: E402
from src.strategy.signal import FLAT, LONG, SHORT, Params, compute   # noqa: E402

DATA = ROOT / "data" / "hourly.parquet"
OUT = ROOT / "docs" / "data.js"


def fmt8(ts) -> str:
    """Format a timestamp as UTC+8 (Taipei) 'YYYY-MM-DD HH:MM'."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return (t.tz_convert("UTC").tz_localize(None) + pd.Timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def main():
    df = pd.read_parquet(DATA)
    cfg = BacktestConfig(start="2022-01-01", end="2026-06-30")
    res = run(df, cfg)
    s = summary(res, df["close"])
    base = cfg.base_notional

    # --- daily equity / buy&hold / drawdown series ------------------------
    eq = res.equity
    daily_eq = eq.resample("1D").last().dropna()
    dd = drawdown_series(eq).resample("1D").last().reindex(daily_eq.index)
    px = df["close"].loc[(df.index >= eq.index[0]) & (df.index <= eq.index[-1])]
    daily_px = px.resample("1D").last().reindex(daily_eq.index).ffill()
    bh0 = daily_px.iloc[0]

    equity_rows = []
    for ts in daily_eq.index:
        equity_rows.append({
            "d": ts.strftime("%Y-%m-%d"),
            "strat": round(float(daily_eq.loc[ts] / base - 1.0) * 100, 2),
            "bh": round(float(daily_px.loc[ts] / bh0 - 1.0) * 100, 2),
            "dd": round(float(dd.loc[ts]) * 100, 2),
        })

    # --- trades -----------------------------------------------------------
    trades = []
    for _, t in res.trades.iterrows():
        trades.append({
            "entry": fmt8(t["entry_time"]),
            "exit": fmt8(t["exit_time"]),
            "side": t["side"],
            "entry_px": round(float(t["entry_px"]), 1),
            "exit_px": round(float(t["exit_px"]), 1),
            "size": round(float(t["size_mult"]), 2),
            "notional": round(float(t["notional"]), 0),
            "ret": round(float(t["gross_return"]) * 100, 2),
            "pnl": round(float(t["pnl"]), 1),
            "win": bool(t["win"]),
        })

    # --- latest signal snapshot ------------------------------------------
    sig = compute(df, cfg.params)
    last = sig.dropna(subset=["pct"]).iloc[-1]
    tgt = int(last["target"])
    latest = {
        "candle": fmt8(sig.dropna(subset=["pct"]).index[-1]) + " UTC+8",
        "lsr": round(float(last["lsr"]), 3),
        "pct": round(float(last["pct"]) * 100, 1),
        "size": round(float(last["size"]), 2),
        "target": {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}[tgt],
        "close": round(float(last["close"]), 1),
    }

    payload = {
        "generated": eq.index[-1].strftime("%Y-%m-%d"),
        "metrics": s,
        "params": {
            "lookback_days": cfg.params.lookback_hours // 24,
            "upper_pct": cfg.params.upper_pct,
            "lower_pct": cfg.params.lower_pct,
            "hold_days": cfg.params.hold_hours // 24,
            "target_daily_vol": cfg.params.target_daily_vol,
            "size_band": [cfg.params.size_min, cfg.params.size_max],
            "fee_rate": cfg.costs.fee_rate,
            "slippage": cfg.costs.slippage,
            "base_notional": base,
        },
        "equity": equity_rows,
        "trades": trades,
        "latest": latest,
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.DASHBOARD = " + json.dumps(payload, default=str) + ";\n")
    print(f"wrote {OUT}  ({len(equity_rows)} daily points, {len(trades)} trades)")


if __name__ == "__main__":
    main()
