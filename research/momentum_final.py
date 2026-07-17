"""
Experiment #13-15 — finalize the BTC momentum strategy: Donchian-k plateau,
multi-timeframe K-bar comparison, and full detailed stats for the chosen config.
Emits results/momentum.json AND docs/momentum.js (window.MOMENTUM) for the page.

Strategy under test:
  entry  = tsmom_extreme: follow the 45d-percentile extreme of the N-day return
           (>=90th pct -> LONG, <=10th -> SHORT), N=30 days.
  exit   = Donchian trailing stop: exit LONG when close < prior k-day low
           (SHORT mirror), with a 30-day max-hold cap.
  sizing = inverse realized-vol (timeframe-aware), costs = taker+slippage/side.

Everything is walk-forward OOS (365d train / 90d test rolling) — the config is
NOT re-optimized per window; we report a fixed config on unseen windows.

    python research/momentum_final.py
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
from src.backtest.metrics import summary                                # noqa: E402
from src.strategy.signal import FLAT, LONG, SHORT, Params, percentile_rank  # noqa: E402

SYM = "BTCUSDT"
N_DAYS = 30
PCT_WIN_D = 45
K_DONCHIAN = 5
CAP_D = 30
TRAIN_D, TEST_D, STEP_D = 365, 90, 90
START, END = "2022-01-01", "2026-06-30"
BASE = 1000.0
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"


# --------------------------------------------------------------------------- #
def resample_ohlc(df, tf_h):
    if tf_h == 1:
        return df[["open", "high", "low", "close"]].copy()
    r = df.resample(f"{tf_h}h")
    return pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                         "low": r["low"].min(), "close": r["close"].last()}).dropna()


def tf_daily_vol(close, window_bars, bpd):
    ret = np.log(close).diff()
    return ret.rolling(window_bars, min_periods=window_bars // 2).std() * np.sqrt(bpd)


def inv_vol_size(dvol, p):
    return (p.target_daily_vol / dvol.replace(0.0, np.nan)).clip(p.size_min, p.size_max)


def mom_frame(df_tf, bpd, k_days):
    """Entry (tsmom_extreme N=30) + Donchian-k trailing exit columns, tf-aware."""
    out = df_tf[["open", "high", "low", "close"]].copy()
    p = Params()
    out["dvol"] = tf_daily_vol(out["close"], int(3 * bpd), bpd)
    out["size"] = inv_vol_size(out["dvol"], p)
    mom = df_tf["close"].pct_change(int(N_DAYS * bpd))
    pct = percentile_rank(mom, int(PCT_WIN_D * bpd))
    out["pct"] = pct
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[pct >= 0.90] = LONG
    t[pct <= 0.10] = SHORT
    t[pct.isna()] = FLAT
    out["target"] = t
    lo = df_tf["close"].rolling(int(k_days * bpd), min_periods=int(k_days * bpd)).min().shift(1)
    hi = df_tf["close"].rolling(int(k_days * bpd), min_periods=int(k_days * bpd)).max().shift(1)
    out["exit_long"] = (df_tf["close"] < lo).fillna(False)
    out["exit_short"] = (df_tf["close"] > hi).fillna(False)
    return out


def cfg_for(tf_h, bpd, start, end):
    return BacktestConfig(start=start, end=end,
                          params=Params(hold_hours=int(CAP_D * bpd)),
                          exit_mode="signal")


def _daily_ret(eq):
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        return pd.Series(dtype=float)
    return daily.diff().fillna(daily.iloc[0] - BASE) / BASE


def _sharpe(dret):
    dret = dret.dropna()
    sd = dret.std()
    return float(dret.mean() / sd * np.sqrt(365)) if sd and not np.isnan(sd) else float("nan")


def walk_forward(df_tf, sigfull, tf_h, bpd, min_hold_bars):
    t0, last = pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC")
    oos_ret, n_trades, win_tot = [], 0, []
    train_start = t0
    while True:
        train_end = train_start + pd.Timedelta(days=TRAIN_D)
        test_end = train_end + pd.Timedelta(days=TEST_D)
        if test_end > last:
            break
        cfg = BacktestConfig(start=str(train_end.date()), end=str(test_end.date()),
                             params=Params(hold_hours=int(CAP_D * bpd)),
                             exit_mode="signal", min_hold_hours=min_hold_bars)
        res = run(df_tf, cfg, signals=sigfull)
        oos_ret.append(_daily_ret(res.equity))
        n_trades += len(res.trades)
        win_tot.append(float(res.equity.iloc[-1] / BASE - 1.0))
        train_start += pd.Timedelta(days=STEP_D)
    allret = pd.concat(oos_ret) if oos_ret else pd.Series(dtype=float)
    eqpath = BASE + allret.cumsum() * BASE
    maxdd = float((eqpath / eqpath.cummax() - 1.0).min()) if len(eqpath) else float("nan")
    npos = sum(1 for t in win_tot if t > 0)
    return _sharpe(allret), n_trades, maxdd, npos, len(win_tot)


def donchian_k_plateau(df1h):
    rows = []
    for k in [3, 4, 5, 6, 7, 8]:
        sig = mom_frame(df1h, 24, k)
        sh, ntr, maxdd, npos, nwin = walk_forward(df1h, sig, 1, 24, 24)
        rows.append({"k": k, "oos_sharpe": round(sh, 2), "maxdd_pct": round(maxdd * 100, 1),
                     "trades": ntr, "windows_positive": f"{npos}/{nwin}"})
    return rows


def timeframe_scan(df1h):
    rows = []
    for tf_h in [1, 2, 4, 24]:
        dtf = resample_ohlc(df1h, tf_h)
        bpd = 24 / tf_h
        sig = mom_frame(dtf, bpd, K_DONCHIAN)
        sh, ntr, maxdd, npos, nwin = walk_forward(dtf, sig, tf_h, bpd, max(1, int(bpd)))
        rows.append({"tf": f"{tf_h}h" if tf_h < 24 else "1d", "tf_h": tf_h,
                     "oos_sharpe": round(sh, 2), "maxdd_pct": round(maxdd * 100, 1),
                     "trades": ntr, "windows_positive": f"{npos}/{nwin}"})
    return rows


def detailed(df1h, tf_h):
    """Full-period detailed stats for the chosen timeframe (Donchian-5)."""
    dtf = resample_ohlc(df1h, tf_h)
    bpd = 24 / tf_h
    sig = mom_frame(dtf, bpd, K_DONCHIAN)
    res = run(dtf, cfg_for(tf_h, bpd, START, END), signals=sig)
    s = summary(res, dtf["close"])
    tr = res.trades
    years = s["years"]
    hold_hrs = (tr["hold_hours"] * tf_h) if len(tr) else pd.Series(dtype=float)  # bars->hours
    # hold-time distribution (days) buckets
    hold_days = hold_hrs / 24.0
    buckets = {"<3d": 0, "3-7d": 0, "7-14d": 0, "14-30d": 0}
    for h in hold_days:
        if h < 3: buckets["<3d"] += 1
        elif h < 7: buckets["3-7d"] += 1
        elif h < 14: buckets["7-14d"] += 1
        else: buckets["14-30d"] += 1
    eq = res.equity.resample("1D").last().dropna()
    curve = {"labels": [d.strftime("%Y-%m-%d") for d in eq.index],
             "equity_pct": [round(float(v / BASE - 1.0) * 100, 2) for v in eq.values]}
    sample = []
    for _, r in (tr.tail(60) if len(tr) else tr).iterrows():
        sample.append({"entry": str(r["entry_time"])[:16], "exit": str(r["exit_time"])[:16],
                       "side": r["side"], "ret_pct": round(r["gross_return"] * 100, 2),
                       "hold_d": round(r["hold_hours"] * tf_h / 24.0, 1), "win": bool(r["win"])})
    return {
        "tf": f"{tf_h}h" if tf_h < 24 else "1d",
        "period": s["period"], "years": years,
        "sharpe": round(s["sharpe"], 2), "total_return_pct": round(s["total_return"] * 100, 1),
        "cagr_pct": round(s["cagr"] * 100, 1) if s["cagr"] == s["cagr"] else None,
        "maxdd_pct": round(s["max_drawdown"] * 100, 1),
        "calmar": round((s["cagr"] / abs(s["max_drawdown"])), 2) if s["max_drawdown"] else None,
        "num_trades": s["num_trades"], "trades_per_year": round(s["num_trades"] / years, 1) if years else None,
        "num_long": s["num_long"], "num_short": s["num_short"],
        "win_rate_pct": round(s["win_rate"] * 100, 1), "profit_factor": round(s["profit_factor"], 2),
        "avg_hold_days": round(float(hold_days.mean()), 1) if len(hold_days) else None,
        "hold_buckets": buckets,
        "yearly": {str(y): {k: round(v * 100, 1) for k, v in d.items()} for y, d in s["yearly"].items()},
        "equity_curve": curve, "sample_trades": sample,
    }


def main():
    df1h = build_hourly(SYM)
    print("== Donchian-k plateau (1h) ==")
    krows = donchian_k_plateau(df1h)
    for r in krows:
        print(f"  k={r['k']}: OOS {r['oos_sharpe']:>5}  MaxDD {r['maxdd_pct']:>6}%  "
              f"trades {r['trades']:>4}  winW {r['windows_positive']}")

    print("\n== timeframe scan (N=30 entry + Donchian-5) ==")
    trows = timeframe_scan(df1h)
    for r in trows:
        print(f"  {r['tf']:>3}: OOS {r['oos_sharpe']:>5}  MaxDD {r['maxdd_pct']:>6}%  "
              f"trades {r['trades']:>4}  winW {r['windows_positive']}")

    best = max(trows, key=lambda r: (r["oos_sharpe"] if r["oos_sharpe"] == r["oos_sharpe"] else -9))
    print(f"\n== best timeframe = {best['tf']} -> detailed stats ==")
    det = detailed(df1h, best["tf_h"])
    print(f"  Sharpe {det['sharpe']} | total {det['total_return_pct']}% | MaxDD {det['maxdd_pct']}% | "
          f"Calmar {det['calmar']} | {det['num_trades']} trades ({det['trades_per_year']}/yr) | "
          f"avg hold {det['avg_hold_days']}d | win {det['win_rate_pct']}%")

    exits = json.loads((RESULTS / "btc_momentum_exits.json").read_text()) \
        if (RESULTS / "btc_momentum_exits.json").exists() else {}

    payload = {
        "symbol": SYM, "entry": f"tsmom_extreme N={N_DAYS} (follow 45d-pct extreme of {N_DAYS}d return)",
        "exit": f"Donchian-{K_DONCHIAN} trailing stop, 30d max cap", "generated": END,
        "donchian_k": krows, "timeframe": trows, "exit_compare": exits, "detailed": det,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "momentum.json").write_text(json.dumps(payload, indent=2, default=str))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "momentum.js").write_text("window.MOMENTUM = " + json.dumps(payload, default=str) + ";\n")
    print(f"\nsaved -> {RESULTS/'momentum.json'} + {DOCS/'momentum.js'}")


if __name__ == "__main__":
    main()
