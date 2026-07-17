"""
Experiment #2 — Catastrophic-only hard stop.

HYPOTHESIS (SOP phase 0):
  The strategy runs NO stop by design (a tight 3-5% stop washes out the slow
  reversion: +74% -> -20%, already tested & rejected). But "no stop" also means
  an idiosyncratic squeeze can ruin a single coin (XRP hit -116% MaxDD = leveraged
  liquidation). A WIDE catastrophic stop (~-20% unlevered price move) should:
    (a) barely trigger on the whitelist (BTC/ADA/DOGE rarely move -20% in 3d) ->
        Sharpe roughly UNCHANGED = it doesn't damage the edge; and
    (b) cap the ruin tail on squeeze-prone coins (XRP) -> MaxDD bounded.
  i.e. cut the left tail without paying the tight-stop tax.

METHOD: engine `catastrophic_stop` (unlevered adverse excursion, fills at stop
level). Fixed 45d lookback. Full-period sweep {0.15,0.20,0.25,0.30, None} on the
whitelist + XRP (ruin coin), plus walk-forward OOS on the whitelist.

    python research/catastrophic_stop.py
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
from src.strategy.signal import Params                       # noqa: E402

WHITELIST = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
RUIN = ["XRPUSDT"]                            # squeeze-prone; no-stop blows up
STOPS = [0.15, 0.20, 0.25, 0.30, None]       # None == no stop (baseline)
LOOKBACK_D = 45
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


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


def _cfg(stop, start, end):
    return BacktestConfig(start=start, end=end,
                          params=Params(lookback_hours=LOOKBACK_D * 24),
                          catastrophic_stop=stop)


def run_full(df, stop) -> dict:
    res = run(df, _cfg(stop, START, END))
    dret = _daily_ret(res.equity)
    tr = res.trades
    # trades whose loss reached ~the stop level (approx: gross <= -stop*0.9)
    n_stopped = int((tr["gross_return"] <= -(stop or 9) * 0.9).sum()) if len(tr) and stop else 0
    return {
        "stop": stop,
        "sharpe": round(_sharpe(dret), 2),
        "total_pct": round(float(res.equity.iloc[-1] / BASE - 1.0) * 100, 1),
        "maxdd_pct": round(_maxdd(res.equity) * 100, 1),
        "trades": int(len(tr)),
        "n_stopped": n_stopped,
        "worst_trade_pct": round(float(tr["gross_return"].min()) * 100, 1) if len(tr) else 0.0,
    }


def walk_forward(df, stop) -> dict:
    t0 = pd.Timestamp(START, tz="UTC")
    last = pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades = [], 0
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        res = run(df, _cfg(stop, str(train_end.date()), str(test_end.date())))
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    return {"stop": stop, "oos_sharpe": round(_sharpe(allret), 2),
            "oos_total_pct": round(float(allret.sum()) * 100, 1), "trades": n_trades}


def main():
    out = {}
    for sym in WHITELIST + RUIN:
        df = build_hourly(sym)
        print(f"\n===== {sym} =====")
        print("  -- full-period sweep (fixed 45d, 2022..2026) --")
        full = [run_full(df, s) for s in STOPS]
        for r in full:
            tag = "no-stop" if r["stop"] is None else f"stop -{int(r['stop']*100)}%"
            print(f"    {tag:>9}: Sharpe {r['sharpe']:>5}  total {r['total_pct']:>7}%  "
                  f"MaxDD {r['maxdd_pct']:>7}%  trades {r['trades']:>3}  "
                  f"stopped {r['n_stopped']:>2}  worst-trade {r['worst_trade_pct']}%")
        wf = None
        if sym in WHITELIST:
            print("  -- walk-forward OOS (fixed 45d) --")
            wf = [walk_forward(df, s) for s in STOPS]
            for r in wf:
                tag = "no-stop" if r["stop"] is None else f"stop -{int(r['stop']*100)}%"
                print(f"    {tag:>9}: OOS Sharpe {r['oos_sharpe']:>5}  OOS total {r['oos_total_pct']:>7}%  "
                      f"trades {r['trades']}")
        out[sym] = {"full_sweep": full, "walk_forward": wf}

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "catastrophic_stop.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'catastrophic_stop.json'}")


if __name__ == "__main__":
    main()
