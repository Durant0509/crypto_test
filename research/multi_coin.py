"""
Run the SAME strategy across a coin universe and compare risk-adjusted return.

Two views per coin:
  1. BASELINE — identical BTC-tuned params (90d lookback, 90/10, 3d hold).
  2. TUNED    — each coin's own best lookback (light per-coin optimisation), to
     see which coins have a *genuine* edge vs which only look bad on BTC's params.

Tests the source report's claim that the edge is strongest on retail-heavy coins
(DOGE/ADA) and weak where institutions dominate. Output feeds the dashboard.

    python research/multi_coin.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                       # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import Params                       # noqa: E402

# ~14-coin universe: majors, L1/L2, retail-heavy meme, exchange token, ruin case.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
           "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "TRXUSDT", "DOTUSDT",
           "SUIUSDT", "1000PEPEUSDT"]
LOOKBACKS = [30, 45, 60, 90, 120]      # per-coin best-lookback search (days)
RESULTS = ROOT / "results"


def _r(x, d=0):
    """Round, but return None for NaN/inf so JSON stays valid."""
    import math
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(float(x), d) if d else round(float(x))


def metrics_row(sym: str, df: pd.DataFrame, params: Params, tag: str) -> dict | None:
    res = run(df, BacktestConfig(start="2022-01-01", end="2026-06-30", params=params))
    if len(res.trades) < 20:               # too few trades to mean anything
        return None
    s = summary(res, df["close"])
    return {
        "symbol": sym, "variant": tag,
        "lookback_days": params.lookback_hours // 24,
        "sharpe": _r(s["sharpe"], 2),
        "total_pct": _r(s["total_return"] * 100),
        "cagr_pct": _r(s["cagr"] * 100),
        "buyhold_pct": _r(s["buy_hold_return"] * 100),
        "maxdd_pct": _r(s["max_drawdown"] * 100, 1),
        "trades": s["num_trades"],
        "days_per_trade": _r(s["years"] * 365 / s["num_trades"], 1),
        "win_pct": _r(s["win_rate"] * 100, 1),
        "pf": _r(s["profit_factor"], 2),
        "corr_btc": _r(s["btc_daily_corr"], 2),
        "worst_mae_pct": _r(res.trades["mae"].min() * 100, 1),
        "data_start": str(df.index.min().date()),
    }


def main():
    baseline, tuned = [], []
    for sym in SYMBOLS:
        try:
            df = build_hourly(sym)
        except Exception as e:                        # noqa: BLE001
            print(f"[{sym}] skipped: {e}"); continue

        b = metrics_row(sym, df, Params(), "baseline")
        if not b:
            print(f"[{sym}] insufficient trades — skipped"); continue
        baseline.append(b)

        # per-coin best lookback (by Sharpe)
        best = None
        for lb in LOOKBACKS:
            r = metrics_row(sym, df, Params(lookback_hours=lb * 24), "tuned")
            if r and (best is None or r["sharpe"] > best["sharpe"]):
                best = r
        if best:
            tuned.append(best)
        print(f"[{sym}] baseline Sharpe {b['sharpe']}  |  tuned Sharpe {best['sharpe']} "
              f"(lookback {best['lookback_days']}d)  |  DD {b['maxdd_pct']}%  "
              f"{b['trades']} trades (~{b['days_per_trade']}d each)")

    base_df = pd.DataFrame(baseline).sort_values("sharpe", ascending=False)
    tuned_df = pd.DataFrame(tuned).sort_values("sharpe", ascending=False)

    RESULTS.mkdir(exist_ok=True)
    base_df.to_csv(RESULTS / "multi_coin.csv", index=False)
    (RESULTS / "multi_coin.json").write_text(json.dumps(
        {"baseline": baseline, "tuned": tuned}, indent=2, default=str))

    print("\n===== BASELINE (BTC-tuned params), sorted by Sharpe =====")
    print(base_df.to_string(index=False))
    print("\n===== TUNED (each coin's best lookback), sorted by Sharpe =====")
    print(tuned_df[["symbol", "lookback_days", "sharpe", "total_pct", "maxdd_pct",
                    "trades", "win_pct", "worst_mae_pct"]].to_string(index=False))
    print(f"\nsaved -> {RESULTS/'multi_coin.csv'} , {RESULTS/'multi_coin.json'}")


if __name__ == "__main__":
    main()
