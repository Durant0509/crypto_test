"""
Signal-discovery scan — evaluate DIFFERENT underlying factors as STANDALONE
signals, each on its own merit (NOT vs the L/S strategy's Sharpe).

Durant's rule (2026-07):
  * same underlying factor -> ok to compare to the old strategy (see #1-3).
  * DIFFERENT underlying factor -> judge on its OWN merit. Any good factor gets
    recorded + paper-simulated. "Good" = standalone OOS Sharpe > 1.2.

Method — hold the TEST HARNESS constant, change only the FACTOR:
  * Harness (identical for every factor): 45d rolling percentile of the factor,
    enter at the 90/10 extremes, 3-day hold, inverse-vol sizing, real costs,
    walk-forward OOS. This is just the rig; the FACTOR is the variable.
  * Direction is pinned by an ECONOMIC THESIS per factor (hypothesis-first, SOP
    phase 0), NOT by cherry-picking whichever direction backtests better.

Scorecard per factor (Sharpe alone hides fat tails on a no-stop strat):
  * OOS Sharpe (walk-forward)   -- primary bar > 1.2
  * IC (Spearman, factor pct vs forward 3d return) + sign-vs-thesis check
  * corr to the L/S sleeve      -- orthogonality; a decent uncorrelated factor is
                                   worth more than a high-Sharpe correlated one
  * MaxDD / Calmar, trades      -- ruin tail + significance (>100 trades)

    python research/factor_scan.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from src.backtest.engine import BacktestConfig, run                     # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params,             # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
LOOKBACK_D = 45
HOLD_H = 72
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
CACHE = ROOT / "data" / "research"
RESULTS = ROOT / "results"

# factor definitions: name -> (column-or-transform, direction, thesis).
# direction "fade" = short at high extreme / long at low; "follow" = the reverse.
# transform: how to turn a raw column into the "value" we percentile-rank.
FACTORS = [
    ("ls_ratio_CONTROL", "count_long_short_ratio", "raw", "fade",
     "retail crowd wrong at extremes (the incumbent — control)"),
    ("taker_vol_ratio", "sum_taker_long_short_vol_ratio", "raw", "fade",
     "aggressive taker buying exhausts -> reversion"),
    ("toptrader_count_ls", "count_toptrader_long_short_ratio", "raw", "fade",
     "top-trader accounts also contrarian at extremes (per prior IC work)"),
    ("toptrader_sum_ls", "sum_toptrader_long_short_ratio", "raw", "fade",
     "top-trader positioning extreme reverts"),
    ("funding", "funding", "raw", "fade",
     "crowded-long pays funding -> mean-reverts (tested weak standalone before)"),
    ("oi_zscore", "sum_open_interest", "zscore", "fade",
     "extreme leverage buildup unwinds -> reversion (weak alone; wants quadrants)"),
    ("oi_change", "sum_open_interest", "pctchange", "fade",
     "OI surging = crowding into a move -> fade the surge"),
    ("price_reversion", "close", "zscore", "fade",
     "price over-extension vs 45d mean reverts"),
    ("price_momentum", "close", "macd", "follow",
     "trend continuation (MACD>0 -> keep going)"),
]


def load_combined(sym: str) -> pd.DataFrame:
    """OHLC (from hourly) + all factor columns + funding, aligned hourly."""
    df = build_hourly(sym)[["open", "high", "low", "close", "lsr"]].copy()
    fac = pd.read_parquet(CACHE / f"{sym}_factors.parquet")
    for c in fac.columns:
        if c != "close":
            df[c] = fac[c].reindex(df.index)
    fund = pd.read_parquet(CACHE / f"{sym}_funding.parquet")
    df["funding"] = fund["funding"].reindex(df.index).ffill(limit=8)
    return df


def make_value(df: pd.DataFrame, col: str, transform: str, window: int) -> pd.Series:
    x = df[col].astype(float)
    if transform == "raw":
        return x
    if transform == "zscore":
        m = x.rolling(window, min_periods=window).mean()
        sd = x.rolling(window, min_periods=window).std()
        return (x - m) / sd.replace(0.0, np.nan)
    if transform == "pctchange":
        return x.pct_change(24)                       # 24h OI rate of change
    if transform == "macd":
        return x.ewm(span=12 * 24).mean() - x.ewm(span=26 * 24).mean()  # daily-ish MACD line
    raise ValueError(transform)


def build_signal_frame(df: pd.DataFrame, value: pd.Series, direction: str,
                       p: Params) -> pd.DataFrame:
    out = df[["open", "high", "low", "close"]].copy()
    pct = percentile_rank(value, p.lookback_hours)
    out["pct"] = pct
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    hi, lo = (SHORT, LONG) if direction == "fade" else (LONG, SHORT)
    target = pd.Series(FLAT, index=out.index, dtype=int)
    target[pct >= p.upper_pct] = hi
    target[pct <= p.lower_pct] = lo
    target[pct.isna()] = FLAT
    out["target"] = target
    return out


def _daily_ret(eq: pd.Series) -> pd.Series:
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret: pd.Series) -> float:
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def factor_ic(df: pd.DataFrame, value: pd.Series) -> float:
    """Spearman IC: factor value at t vs forward 3d return. Signed."""
    fwd = df["close"].shift(-HOLD_H) / df["close"] - 1.0
    v = value.reindex(df.index)
    both = pd.concat([v, fwd], axis=1).dropna()
    if len(both) < 500:
        return float("nan")
    return float(both.iloc[:, 0].rank().corr(both.iloc[:, 1].rank()))


def walk_forward(df: pd.DataFrame, value: pd.Series, direction: str, p: Params):
    """Returns (oos_daily_returns Series, n_trades, maxdd, oos_equity_end)."""
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades = [], 0
    eq_chain = [BASE]
    train_start = t0
    sigfull = build_signal_frame(df, value, direction, p)
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        cfg = BacktestConfig(start=str(train_end.date()), end=str(test_end.date()), params=p)
        res = run(df, cfg, signals=sigfull)
        dret = _daily_ret(res.equity)
        oos_ret.append(dret)
        n_trades += len(res.trades)
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    # reconstruct an OOS equity path (fixed-notional additive) for MaxDD
    eqpath = BASE + (allret.cumsum() * BASE)
    peak = eqpath.cummax()
    maxdd = float((eqpath / peak - 1.0).min()) if len(eqpath) else float("nan")
    return allret, n_trades, maxdd


def main():
    p = Params(lookback_hours=LOOKBACK_D * 24, hold_hours=HOLD_H)
    out = {}
    for sym in COINS:
        df = load_combined(sym)
        print(f"\n===== {sym} =====")
        # baseline L/S sleeve daily returns for orthogonality corr
        ls_val = make_value(df, "count_long_short_ratio", "raw", p.lookback_hours)
        ls_ret, _, _ = walk_forward(df, ls_val, "fade", p)

        print(f"  {'factor':<20} {'dir':<6} {'OOSsharpe':>9} {'IC':>7} {'corrLS':>7} "
              f"{'MaxDD%':>7} {'trades':>6}  flag")
        rows = []
        for name, col, tf, direction, thesis in FACTORS:
            value = make_value(df, col, tf, p.lookback_hours)
            dret, ntr, maxdd = walk_forward(df, value, direction, p)
            sh = _sharpe(dret)
            ic = factor_ic(df, value)
            # corr of this factor's OOS daily returns to the L/S sleeve
            common = dret.dropna().index.intersection(ls_ret.dropna().index)
            corr = (float(np.corrcoef(dret.loc[common], ls_ret.loc[common])[0, 1])
                    if len(common) > 10 else float("nan"))
            good = (not np.isnan(sh)) and sh > 1.2 and ntr >= 100
            flag = "RECORD+SIM" if good else ("orthogonal?" if abs(corr) < 0.3 and sh > 0.6 else "")
            print(f"  {name:<20} {direction:<6} {sh:>9.2f} {ic:>7.3f} {corr:>7.2f} "
                  f"{maxdd*100:>7.1f} {ntr:>6}  {flag}")
            rows.append({"factor": name, "direction": direction, "thesis": thesis,
                         "oos_sharpe": round(sh, 2), "ic": round(ic, 3),
                         "corr_to_ls": round(corr, 2), "maxdd_pct": round(maxdd * 100, 1),
                         "trades": ntr, "good": good})
        out[sym] = rows
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "factor_scan.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'factor_scan.json'}")
    print("\nflag legend: RECORD+SIM = OOS Sharpe>1.2 & >100 trades (Durant's bar). "
          "orthogonal? = low corr to L/S + Sharpe>0.6 (diversifier candidate).")


if __name__ == "__main__":
    main()
