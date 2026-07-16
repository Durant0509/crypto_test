"""
Coinbase Premium as a filter on the retail L/S strategy — STEP 2, per SOP,
after IC/IR (coinbase_premium.py showed premium is POSITIVE-IC = bullish
confirmation, orthogonal & opposite-sign to the contrarian retail factor).

Hypothesis to test: fade retail, but only when institutional spot flow AGREES
with our contrarian side —
  * SHORT (retail crowded long) only if premium is LOW (institutions NOT bidding)
  * LONG  (retail crowded short) only if premium is HIGH (institutions bidding)
i.e. skip the fade when smart US spot money is on the crowd's side.

Variants (45d lookback, else identical):
  baseline           retail extreme only
  gate .50           short if prem_pct<=.50 ; long if prem_pct>=.50
  gate lenient .60/.40   short if prem_pct<=.60 ; long if prem_pct>=.40 (filters only extremes)
  gate strict  .40/.60   short if prem_pct<=.40 ; long if prem_pct>=.60

Winner (if any) then walk-forward validated before we believe it.

    python research/coinbase_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.coinbase_premium import premium_frame          # noqa: E402
from research.lib import build_hourly                         # noqa: E402
from src.backtest.engine import BacktestConfig, run           # noqa: E402
from src.backtest.metrics import summary                      # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params,    # noqa: E402
                                  compute, percentile_rank)

LOOKBACK_D = 45
COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
RESULTS = ROOT / "results"
# (name, short_gate_hi, long_gate_lo)  short allowed if prem_pct<=hi; long if prem_pct>=lo
VARIANTS = [
    ("baseline",       None, None),
    ("gate .50",       0.50, 0.50),
    ("gate len .60/.40", 0.60, 0.40),
    ("gate str .40/.60", 0.40, 0.60),
]


def frame(sym):
    base = build_hourly(sym)                       # OHLCV + retail lsr
    prem = premium_frame(sym)[["premium_bp"]]
    df = base.join(prem, how="left")
    df["premium_bp"] = df["premium_bp"].ffill(limit=6)
    return df


def make_sig(df, p, hi, lo):
    sig = compute(df, p)
    rpct = sig["pct"]
    ppct = percentile_rank(df["premium_bp"], p.lookback_hours)
    tgt = pd.Series(FLAT, index=sig.index, dtype=int)
    short = rpct >= p.upper_pct
    long_ = rpct <= p.lower_pct
    if hi is not None:
        short = short & (ppct <= hi)
        long_ = long_ & (ppct >= lo)
        bad_extra = ppct.isna() | df["premium_bp"].isna()
    else:
        bad_extra = pd.Series(False, index=sig.index)
    tgt[short] = SHORT
    tgt[long_] = LONG
    tgt[rpct.isna() | df["lsr"].isna() | bad_extra] = FLAT
    sig = sig.copy(); sig["target"] = tgt
    return sig


def evalv(df, sig):
    res = run(df, BacktestConfig(start="2022-01-01", end="2026-06-30"), signals=sig)
    if len(res.trades) < 20:
        return None
    s = summary(res, df["close"])
    return {"sharpe": round(s["sharpe"], 2), "total_pct": round(s["total_return"] * 100),
            "maxdd_pct": round(s["max_drawdown"] * 100, 1), "trades": s["num_trades"],
            "days_per_trade": round(s["years"] * 365 / s["num_trades"], 1),
            "win_pct": round(s["win_rate"] * 100, 1)}


def walk_forward(df, hi, lo):
    t0, last = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2026-06-30", tz="UTC")
    p = Params(lookback_hours=LOOKBACK_D * 24)
    rets, ts = [], t0
    while True:
        s0 = ts + pd.Timedelta(days=365); s1 = s0 + pd.Timedelta(days=90)
        if s1 > last:
            break
        sig = make_sig(df, p, hi, lo)
        res = run(df, BacktestConfig(start=str(s0.date()), end=str(s1.date()),
                                     params=p), signals=sig)
        daily = res.equity.resample("1D").last().dropna()
        if len(daily) > 1:
            rets.append(daily.diff().fillna(daily.iloc[0] - 1000.0) / 1000.0)
        ts += pd.Timedelta(days=90)
    r = pd.concat(rets).dropna() if rets else pd.Series(dtype=float)
    sd = r.std()
    return round(float(r.mean() / sd * np.sqrt(365)), 2) if sd else None


def main():
    out = {}
    p = Params(lookback_hours=LOOKBACK_D * 24)
    for sym in COINS:
        df = frame(sym)
        rows, wf = {}, {}
        for name, hi, lo in VARIANTS:
            rows[name] = evalv(df, make_sig(df, p, hi, lo))
            wf[name] = walk_forward(df, hi, lo)
        out[sym] = {"in_sample": rows, "oos_sharpe": wf}
        print(f"\n===== {sym} (lookback {LOOKBACK_D}d) =====")
        print(f"{'variant':<18}{'sharpe':>7}{'total%':>8}{'maxDD%':>8}{'trades':>7}{'win%':>7}{'OOS_sharpe':>11}")
        for name, *_ in VARIANTS:
            r = rows[name]
            if not r: print(f"{name:<18}  (too few)"); continue
            print(f"{name:<18}{r['sharpe']:>7}{r['total_pct']:>8}{r['maxdd_pct']:>8}"
                  f"{r['trades']:>7}{r['win_pct']:>7}{str(wf[name]):>11}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "coinbase_backtest.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'coinbase_backtest.json'}")


if __name__ == "__main__":
    main()
