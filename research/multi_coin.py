"""
Run the SAME strategy (identical params) across several coins and compare Sharpe.

Tests the source report's claim: the edge should be strongest on retail-heavy
coins (DOGE, ADA) and weaker where institutions dominate (BTC, ETH).

    python research/multi_coin.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                       # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]
RESULTS = ROOT / "results"


def main():
    rows = []
    for sym in SYMBOLS:
        print(f"[{sym}] building data...")
        df = build_hourly(sym)
        cfg = BacktestConfig(start="2022-01-01", end="2026-06-30")
        res = run(df, cfg)
        if len(res.trades) == 0:
            print(f"  {sym}: no trades (insufficient data)"); continue
        s = summary(res, df["close"])
        rows.append({
            "symbol": sym,
            "sharpe": round(s["sharpe"], 2),
            "total_%": round(s["total_return"] * 100, 0),
            "buyhold_%": round(s["buy_hold_return"] * 100, 0),
            "maxDD_%": round(s["max_drawdown"] * 100, 1),
            "trades": s["num_trades"],
            "win_%": round(s["win_rate"] * 100, 1),
            "pf": round(s["profit_factor"], 2),
            "corr_btc": round(s["btc_daily_corr"], 2),
            "period": s["period"],
        })
        print(f"  {sym}: Sharpe {s['sharpe']:.2f}, total {s['total_return']*100:+.0f}%, "
              f"DD {s['max_drawdown']*100:.0f}%, {s['num_trades']} trades")

    out = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    RESULTS.mkdir(exist_ok=True)
    out.to_csv(RESULTS / "multi_coin.csv", index=False)
    print("\n===== Multi-coin comparison (same params, sorted by Sharpe) =====")
    print(out.to_string(index=False))
    print(f"\nsaved -> {RESULTS/'multi_coin.csv'}")


if __name__ == "__main__":
    main()
