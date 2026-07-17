"""
Experiment #1 — ADX regime filter for the Retail L/S Reversion strategy.

HYPOTHESIS (SOP phase 0 — who loses money to us / why this should work):
  Fading the crowd is a MEAN-REVERSION bet. It should print money when the
  market is ranging (the crowd's extreme lean gets punished by chop) and get
  run over when the market is in a strong directional trend (the crowd is
  *right*, price keeps going, "there is a floor below the basement"). ADX
  measures trend strength. So: only take reversion entries when ADX is LOW
  (ranging); stand aside when ADX is HIGH (strong trend). We are not adding an
  orthogonal alpha factor — we are gating the SAME single factor by regime.
  This is a MECHANICS change (like 45d lookback / normalize exit), the category
  that has actually raised OOS Sharpe historically, not a confirming factor.

METHOD:
  * ADX computed on DAILY bars (Wilder 14) — the strategy holds ~3d over a 45d
    lookback, so multi-day trend strength is the right regime scale, not 14h.
  * Causal: use the PRIOR day's ADX (shift 1d), forward-filled onto the hourly
    index. No look-ahead — the gate at hour t only knows fully-closed data.
  * Gate: force target=FLAT on any bar whose regime ADX >= threshold.
  * Engine unchanged: we pass a pre-computed `signals` frame via run(signals=).

VALIDATION (SOP phase 2):
  1. Full-period sweep over thresholds {18,22,25,30,100(off)} per coin — look for
     a PLATEAU of improvement, not a lone spike.
  2. Walk-forward OOS (same rolling windows as walk_forward.py, fixed 45d
     lookback for BOTH arms) comparing baseline vs ADX-gated on identical unseen
     windows. Adopt ONLY if OOS Sharpe beats baseline across coins + trades stay
     healthy.

    python research/adx_regime.py
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
from src.strategy.signal import FLAT, Params, compute        # noqa: E402

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT", "ETHUSDT"]
THRESHOLDS = [18, 22, 25, 30, 100]           # 100 == filter effectively OFF (baseline)
LOOKBACK_D = 45                              # validated default; fixed for both arms
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"


# --------------------------------------------------------------------------- #
# ADX (Wilder) on daily bars, mapped causally onto the hourly index.
# --------------------------------------------------------------------------- #
def _wilder(s: pd.Series, period: int) -> pd.Series:
    # Wilder's RMA == ewm with alpha = 1/period, no bias correction.
    return s.ewm(alpha=1.0 / period, adjust=False).mean()


def adx_daily(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX on daily OHLC resampled from the hourly frame."""
    d = pd.DataFrame({
        "high": df["high"].resample("1D").max(),
        "low": df["low"].resample("1D").min(),
        "close": df["close"].resample("1D").last(),
    }).dropna()

    up = d["high"].diff()
    down = -d["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    prev_close = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = _wilder(tr, period)
    plus_di = 100.0 * _wilder(pd.Series(plus_dm, index=d.index), period) / atr
    minus_di = 100.0 * _wilder(pd.Series(minus_dm, index=d.index), period) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return _wilder(dx.fillna(0.0), period).rename("adx")


def adx_hourly_causal(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Prior-day ADX broadcast onto the hourly index (strictly causal)."""
    a = adx_daily(df, period).shift(1)           # only use fully-closed prior day
    return a.reindex(df.index, method="ffill")


def gated_signals(df: pd.DataFrame, params: Params, adx_h: pd.Series,
                  threshold: float) -> pd.DataFrame:
    """Standard signal frame with target forced FLAT where regime ADX >= threshold."""
    sig = compute(df, params)
    if threshold < 100:
        block = adx_h.reindex(sig.index) >= threshold
        sig.loc[block.fillna(False), "target"] = FLAT
    return sig


# --------------------------------------------------------------------------- #
# metric helpers (match walk_forward.py conventions)
# --------------------------------------------------------------------------- #
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


def run_full(df: pd.DataFrame, adx_h: pd.Series, threshold: float,
             start: str, end: str) -> dict:
    params = Params(lookback_hours=LOOKBACK_D * 24)
    cfg = BacktestConfig(start=start, end=end, params=params)
    sig = gated_signals(df, params, adx_h, threshold)
    res = run(df, cfg, signals=sig)
    dret = _daily_ret(res.equity)
    return {
        "threshold": threshold,
        "sharpe": round(_sharpe(dret), 2),
        "total_pct": round(float(res.equity.iloc[-1] / BASE - 1.0) * 100, 1),
        "maxdd_pct": round(_maxdd(res.equity) * 100, 1),
        "trades": int(len(res.trades)),
        "win_rate": round(float(res.trades["win"].mean()) * 100, 1) if len(res.trades) else 0.0,
    }


def walk_forward(df: pd.DataFrame, adx_h: pd.Series, threshold: float) -> dict:
    """Fixed-45d walk-forward; concatenate OOS test-window daily returns."""
    params = Params(lookback_hours=LOOKBACK_D * 24)
    t0 = pd.Timestamp(START, tz="UTC")
    last = pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades, n_pos, n_win = [], 0, 0, 0

    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        cfg = BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                             params=params)
        sig = gated_signals(df, params, adx_h, threshold)
        res = run(df, cfg, signals=sig)
        dret = _daily_ret(res.equity)
        oos_ret.append(dret)
        n_trades += len(res.trades)
        tot = float(res.equity.iloc[-1] / BASE - 1.0)
        n_win += 1
        n_pos += 1 if tot > 0 else 0
        train_start += pd.Timedelta(days=STEP_D)

    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    return {
        "threshold": threshold,
        "oos_sharpe": round(_sharpe(allret), 2),
        "oos_total_pct": round(float(allret.sum()) * 100, 1),
        "trades": n_trades,
        "windows_positive": f"{n_pos}/{n_win}",
    }


def main():
    out = {}
    for sym in COINS:
        df = build_hourly(sym)
        adx_h = adx_hourly_causal(df)
        print(f"\n===== {sym} =====")

        print("  -- full-period threshold sweep (fixed 45d, 2022..2026) --")
        full = [run_full(df, adx_h, th, START, END) for th in THRESHOLDS]
        for r in full:
            tag = "OFF/baseline" if r["threshold"] == 100 else f"ADX<{r['threshold']}"
            print(f"    {tag:>13}: Sharpe {r['sharpe']:>5}  total {r['total_pct']:>7}%  "
                  f"MaxDD {r['maxdd_pct']:>6}%  trades {r['trades']:>3}  win {r['win_rate']}%")

        print("  -- walk-forward OOS (fixed 45d, same rolling windows) --")
        wf = [walk_forward(df, adx_h, th) for th in THRESHOLDS]
        for r in wf:
            tag = "OFF/baseline" if r["threshold"] == 100 else f"ADX<{r['threshold']}"
            print(f"    {tag:>13}: OOS Sharpe {r['oos_sharpe']:>5}  OOS total {r['oos_total_pct']:>7}%  "
                  f"trades {r['trades']:>3}  win-windows {r['windows_positive']}")

        out[sym] = {"full_sweep": full, "walk_forward": wf}

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "adx_regime.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'adx_regime.json'}")


if __name__ == "__main__":
    main()
