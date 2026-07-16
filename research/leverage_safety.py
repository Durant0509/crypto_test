"""
Leverage safety / liquidation analysis for the Retail L/S Reversion strategy.

The strategy has NO stop-loss, so the only thing standing between a bad trade and
a blown account is leverage. This answers the concrete question: *"how many times
leverage can I run before a historical trade would have liquidated me?"*

Method (honest, per-position, isolated-margin):
  * For every trade we already record `mae` — the worst intra-hold price move
    against the position (from hourly high/low), UNLEVERED.
  * A position opened at leverage L (isolated margin) is liquidated when the
    adverse price move reaches ~ 1/L minus the maintenance-margin rate:
        liq_move(L) = 1/L - mmr
    So a trade with |mae| >= liq_move(L) would have been liquidated at leverage L.
  * Max historical-safe leverage = the largest L at which ZERO trades liquidate.
  * Recommended leverage applies a safety factor (the historical worst is NOT the
    future worst — drawdowns can exceed history) and respects the book rule of
    perp leverage <= 3-5x.

Caveats surfaced in the output: hourly high/low can understate a real intra-bar
spike; this is single-exchange Binance data; vol-sizing (size_mult) changes how
much CAPITAL is deployed but not the per-position liquidation price.

    python research/leverage_safety.py
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

RESULTS = ROOT / "results"
MMR = 0.005                       # ~Binance USD-M BTC maintenance margin (low tier)
LEVERAGES = [1, 2, 3, 5, 7, 10, 15, 20, 25]
SAFETY_FACTOR = 2.0               # recommend at most 1/2 of the historical-max-safe


def liq_move(lev: float) -> float:
    """Adverse price move that liquidates an isolated position at leverage `lev`."""
    return max(1.0 / lev - MMR, 1e-9)


def analyse(trades: pd.DataFrame) -> dict:
    mae = trades["mae"].astype(float)          # <= 0
    worst = float(mae.min())
    n = len(trades)

    table = []
    max_safe = 0
    for lev in LEVERAGES:
        lm = liq_move(lev)
        liquidated = int((mae <= -lm).sum())
        table.append({
            "leverage": lev,
            "liq_move_pct": round(lm * 100, 1),      # % adverse move that liquidates
            "liquidated_trades": liquidated,
            "liquidated_pct": round(liquidated / n * 100, 2),
            "survives": liquidated == 0,
        })
        if liquidated == 0:
            max_safe = lev

    # theoretical ceiling from the single worst trade, then a buffered recommendation.
    # Buffer below the historical-safe max because the worst trade in history is NOT
    # the future ceiling; house-cap at 3x for a NO-STOP strategy (stricter than the
    # book's 5x general perp cap, since a bad trade has no stop to save it).
    theoretical_max = round(1.0 / (abs(worst) + MMR), 1) if worst < 0 else float("inf")
    if max_safe == 0:
        recommended, verdict = 0, "avoid — liquidates even at 1x (idiosyncratic ruin risk)"
    else:
        recommended = min(3, max(1, round(max_safe * 0.6)))
        verdict = f"up to {recommended}x (historical-safe ceiling {max_safe}x, buffered)"

    # adverse-excursion distribution (for the histogram / context)
    buckets = []
    for thr in [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
        c = int((mae <= -thr).sum())
        buckets.append({"worse_than_pct": int(thr * 100), "trades": c,
                        "pct": round(c / n * 100, 1)})

    return {
        "num_trades": n,
        "worst_mae_pct": round(worst * 100, 1),
        "median_mae_pct": round(float(mae.median()) * 100, 1),
        "mean_mae_pct": round(float(mae.mean()) * 100, 1),
        "p95_mae_pct": round(float(mae.quantile(0.05)) * 100, 1),  # 5th pct = 95% worst
        "theoretical_max_leverage": theoretical_max,
        "max_safe_leverage": max_safe,
        "recommended_leverage": recommended,
        "verdict": verdict,
        "mmr": MMR,
        "table": table,
        "buckets": buckets,
    }


def for_symbol(sym: str, params: Params | None = None) -> dict:
    df = build_hourly(sym)
    cfg = BacktestConfig(start="2022-01-01", end="2026-06-30",
                         params=params or Params())
    res = run(df, cfg)
    s = summary(res, df["close"])
    a = analyse(res.trades)
    a.update(symbol=sym, sharpe=round(s["sharpe"], 2),
             max_drawdown_pct=round(s["max_drawdown"] * 100, 1))
    return a


def main():
    # primary analysis on BTC baseline params
    btc = for_symbol("BTCUSDT")
    print("===== Leverage safety — BTCUSDT (baseline params) =====")
    print(f"trades {btc['num_trades']}  worst single-trade MAE {btc['worst_mae_pct']}%  "
          f"median {btc['median_mae_pct']}%")
    print(f"theoretical max leverage {btc['theoretical_max_leverage']}x  "
          f"max-safe (0 liquidations) {btc['max_safe_leverage']}x  "
          f"RECOMMENDED {btc['recommended_leverage']}x")
    print(f"\n{'lev':>4} {'liq@move':>9} {'liquidated':>11} {'survives':>9}")
    for r in btc["table"]:
        print(f"{r['leverage']:>3}x {r['liq_move_pct']:>7}% {r['liquidated_trades']:>6} "
              f"({r['liquidated_pct']:>4}%) {'YES' if r['survives'] else 'NO':>7}")

    # cross-coin leverage safety (why XRP is a ruin coin)
    coins = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT", "XRPUSDT"]
    per_coin = []
    for sym in coins:
        try:
            a = for_symbol(sym)
            per_coin.append({k: a[k] for k in
                             ["symbol", "worst_mae_pct", "max_safe_leverage",
                              "recommended_leverage", "sharpe", "max_drawdown_pct"]})
        except Exception as e:                       # noqa: BLE001
            print(f"  {sym}: skipped ({e})")

    print("\n===== Leverage safety by coin =====")
    print(pd.DataFrame(per_coin).to_string(index=False))

    RESULTS.mkdir(exist_ok=True)
    out = {"btc": btc, "by_coin": per_coin,
           "notes": {"mmr": MMR, "safety_factor": SAFETY_FACTOR,
                     "method": "isolated-margin per-position; liq at 1/L - mmr adverse "
                               "price move; MAE from hourly high/low (real ticks may be worse)"}}
    (RESULTS / "leverage_safety.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'leverage_safety.json'}")


if __name__ == "__main__":
    main()
