"""
Multi-factor confluence test — STEP 2, per STRATEGY_SOP.md, AFTER IC/IR (factors.py).

IC/IR finding: retail L/S AND top-trader L/S are BOTH contrarian (negative IC) and
move together — so the original "divergence" hypothesis is refuted. Revised
hypothesis to test here: **agreement/reinforcement** — only fade when retail AND
top-traders are BOTH crowded the same way (higher conviction, fewer trades).

Variants (all: 45d lookback, 3d hold, no stop, inverse-vol sizing — only the
ENTRY GATE changes):
  * baseline   — retail L/S extreme only (the validated current signal)
  * agree      — retail extreme AND top-trader(positions) also extreme SAME side
  * blend      — trigger on the extreme of the AVERAGE of retail & top-trader pct

Compared honestly vs baseline on the same window; the winner (if any) is then
walk-forward validated in walk_forward-style before we believe it.

    python research/confluence.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.factors import build_enriched                 # noqa: E402
from research.lib import build_hourly                        # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params,   # noqa: E402
                                  compute, percentile_rank)

LOOKBACK_D = 45
TOP = "sum_toptrader_long_short_ratio"      # top-trader positions L/S (strongest |IC|)
COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
RESULTS = ROOT / "results"


def enriched_frame(sym: str) -> pd.DataFrame:
    """OHLCV + retail lsr (base) joined with the top-trader ratio."""
    base = build_hourly(sym)                                 # open/high/low/close/volume/lsr
    top = build_enriched(sym)[[TOP]]
    df = base.join(top, how="left")
    df[TOP] = df[TOP].ffill(limit=6)
    return df


def make_signals(df: pd.DataFrame, p: Params, variant: str) -> pd.DataFrame:
    """Reuse compute() for size/vol + retail pct, then override target per variant."""
    sig = compute(df, p)                                     # gives pct (retail), size, dvol
    retail_pct = sig["pct"]
    top_pct = percentile_rank(df[TOP], p.lookback_hours)
    tgt = pd.Series(FLAT, index=sig.index, dtype=int)

    if variant == "baseline":
        tgt[retail_pct >= p.upper_pct] = SHORT
        tgt[retail_pct <= p.lower_pct] = LONG
    elif variant == "agree":
        tgt[(retail_pct >= p.upper_pct) & (top_pct >= p.upper_pct)] = SHORT
        tgt[(retail_pct <= p.lower_pct) & (top_pct <= p.lower_pct)] = LONG
    elif variant == "blend":
        avg = (retail_pct + top_pct) / 2.0
        tgt[avg >= p.upper_pct] = SHORT
        tgt[avg <= p.lower_pct] = LONG
    else:
        raise ValueError(variant)

    # no signal where required inputs are missing. baseline needs only retail;
    # the confluence variants additionally require the top-trader factor.
    bad = retail_pct.isna() | df["lsr"].isna()
    if variant != "baseline":
        bad = bad | top_pct.isna() | df[TOP].isna()
    tgt[bad] = FLAT
    sig = sig.copy()
    sig["target"] = tgt
    return sig


def evalv(df, sig, price):
    res = run(df, BacktestConfig(start="2022-01-01", end="2026-06-30"), signals=sig)
    if len(res.trades) < 20:
        return None
    s = summary(res, price)
    return {"sharpe": round(s["sharpe"], 2), "total_pct": round(s["total_return"] * 100),
            "maxdd_pct": round(s["max_drawdown"] * 100, 1), "trades": s["num_trades"],
            "days_per_trade": round(s["years"] * 365 / s["num_trades"], 1),
            "win_pct": round(s["win_rate"] * 100, 1), "pf": round(s["profit_factor"], 2),
            "worst_mae_pct": round(res.trades["mae"].min() * 100, 1)}


def main():
    p = Params(lookback_hours=LOOKBACK_D * 24)
    out = {}
    for sym in COINS:
        df = enriched_frame(sym)
        cov = df[TOP].notna().mean()
        row = {}
        for v in ["baseline", "agree", "blend"]:
            sig = make_signals(df, p, v)
            row[v] = evalv(df, sig, df["close"])
        out[sym] = {"top_coverage": round(float(cov), 3), **row}
        print(f"\n===== {sym}  (top-trader coverage {cov:.0%}, lookback {LOOKBACK_D}d) =====")
        print(f"{'variant':<10}{'sharpe':>8}{'total%':>8}{'maxDD%':>8}{'trades':>8}{'d/trade':>8}{'win%':>7}{'MAE%':>8}")
        for v in ["baseline", "agree", "blend"]:
            r = row[v]
            if not r: print(f"{v:<10}  (too few trades)"); continue
            print(f"{v:<10}{r['sharpe']:>8}{r['total_pct']:>8}{r['maxdd_pct']:>8}"
                  f"{r['trades']:>8}{r['days_per_trade']:>8}{r['win_pct']:>7}{r['worst_mae_pct']:>8}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "confluence.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'confluence.json'}")


if __name__ == "__main__":
    main()
