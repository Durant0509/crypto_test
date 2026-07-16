"""
Funding-rate factor — next candidate, per STRATEGY_SOP.md: IC/IR first, then
backtest, then walk-forward; only believe what holds out-of-sample.

Funding rate = the periodic (8h) payment between longs & shorts on the perp. It's
a "$-cost" crowding measure (vs the retail L/S "head-count" measure). Extreme
positive funding = longs over-crowded and PAYING to be long -> mean-reversion /
liquidation risk. Hypothesis: like retail L/S it should be NEGATIVE-IC (fade), but
semi-orthogonal (cost vs head-count), so it may confirm/improve the signal.

Data: data.binance.vision monthly fundingRate dumps (cols calc_time,
funding_interval_hours, last_funding_rate), resampled to hourly (ffill the 8h rate).

Factors tested: funding rate level, cumulative funding over the 3-day hold.
Backtest variants (45d lookback): baseline (retail) / funding-standalone reversion /
retail+funding agreement confluence.

    python research/funding.py
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.factors import spearman                       # noqa: E402
from research.lib import build_hourly                        # noqa: E402
from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params,   # noqa: E402
                                  compute, percentile_rank)

CACHE = ROOT / "data" / "research"
RESULTS = ROOT / "results"
BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
LOOKBACK_D = 45
HOLD_H = 72


def fetch_funding(sym: str, start="2021-10", end="2026-06") -> pd.Series:
    out = CACHE / f"{sym}_funding.parquet"
    if out.exists():
        return pd.read_parquet(out)["funding"]
    s = requests.Session(); s.headers.update({"User-Agent": "crypto_test/1.0"})
    y0, m0 = map(int, start.split("-")); y1, m1 = map(int, end.split("-"))
    frames = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        url = f"{BASE}/{sym}/{sym}-fundingRate-{y:04d}-{m:02d}.zip"
        r = s.get(url, timeout=30)
        if r.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(r.content)); n = z.namelist()[0]
            df = pd.read_csv(z.open(n))
            df["ts"] = pd.to_datetime(df["calc_time"], unit="ms", utc=True)
            frames.append(df.set_index("ts")["last_funding_rate"])
        m += 1
        if m > 12:
            m, y = 1, y + 1
    fr = pd.concat(frames).sort_index()
    fr = fr[~fr.index.duplicated(keep="first")]
    hourly = fr.resample("1h").ffill()                    # 8h rate carried hourly
    df = hourly.to_frame("funding")
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return df["funding"]


def frame(sym: str) -> pd.DataFrame:
    df = build_hourly(sym)                                # OHLCV + retail lsr
    f = fetch_funding(sym).rename("funding")
    df = df.join(f, how="left")
    df["funding"] = df["funding"].ffill(limit=8)
    df["cum_funding"] = df["funding"].rolling(HOLD_H, min_periods=8).sum()
    return df


def ic_report(sym: str) -> dict:
    df = frame(sym).copy()
    df["fwd"] = df["close"].shift(-HOLD_H) / df["close"] - 1.0
    out = {"symbol": sym}
    for col in ["funding", "cum_funding"]:
        sub = df[[col, "fwd"]].replace([np.inf, -np.inf], np.nan).dropna()
        ic = spearman(sub[col], sub["fwd"]) if len(sub) > 2000 else None
        block, ics = 720, []
        for i in range(0, len(sub) - block, block):
            b = sub.iloc[i:i + block]
            if len(b) > 100:
                c = spearman(b[col], b["fwd"])
                if not np.isnan(c):
                    ics.append(c)
        ics = np.array(ics)
        out[col] = {"ic_full": round(float(ic), 4) if ic is not None else None,
                    "ic_mean_30d": round(float(ics.mean()), 4) if len(ics) else None,
                    "ir": round(float(ics.mean() / ics.std()), 2) if len(ics) and ics.std() else None}
    return out


def make_sig(df, p, variant):
    sig = compute(df, p)
    rpct = sig["pct"]
    fpct = percentile_rank(df["funding"], p.lookback_hours)
    tgt = pd.Series(FLAT, index=sig.index, dtype=int)
    if variant == "baseline":
        tgt[rpct >= p.upper_pct] = SHORT; tgt[rpct <= p.lower_pct] = LONG
        bad = rpct.isna()
    elif variant == "funding_only":     # fade funding-percentile extremes
        tgt[fpct >= p.upper_pct] = SHORT; tgt[fpct <= p.lower_pct] = LONG
        bad = fpct.isna() | df["funding"].isna()
    elif variant == "agree":            # retail AND funding both extreme same side
        tgt[(rpct >= p.upper_pct) & (fpct >= p.upper_pct)] = SHORT
        tgt[(rpct <= p.lower_pct) & (fpct <= p.lower_pct)] = LONG
        bad = rpct.isna() | fpct.isna() | df["funding"].isna()
    tgt[bad | df["lsr"].isna()] = FLAT
    sig = sig.copy(); sig["target"] = tgt
    return sig


def evalv(df, sig, s0="2022-01-01", s1="2026-06-30"):
    res = run(df, BacktestConfig(start=s0, end=s1), signals=sig)
    if len(res.trades) < 20:
        return None
    s = summary(res, df["close"])
    return {"sharpe": round(s["sharpe"], 2), "total_pct": round(s["total_return"] * 100),
            "maxdd_pct": round(s["max_drawdown"] * 100, 1), "trades": s["num_trades"],
            "win_pct": round(s["win_rate"] * 100, 1)}


def walk_forward(df, variant):
    t0, last = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2026-06-30", tz="UTC")
    p = Params(lookback_hours=LOOKBACK_D * 24); rets, ts = [], t0
    while True:
        a = ts + pd.Timedelta(days=365); b = a + pd.Timedelta(days=90)
        if b > last:
            break
        sig = make_sig(df, p, variant)
        res = run(df, BacktestConfig(start=str(a.date()), end=str(b.date()), params=p), signals=sig)
        daily = res.equity.resample("1D").last().dropna()
        if len(daily) > 1:
            rets.append(daily.diff().fillna(daily.iloc[0] - 1000.0) / 1000.0)
        ts += pd.Timedelta(days=90)
    r = pd.concat(rets).dropna() if rets else pd.Series(dtype=float)
    sd = r.std()
    return round(float(r.mean() / sd * np.sqrt(365)), 2) if sd else None


def main():
    p = Params(lookback_hours=LOOKBACK_D * 24)
    out = {}
    print("=== Funding IC (vs forward 72h) ===")
    for sym in COINS:
        ic = ic_report(sym)
        print(f"{sym:<9} funding IC {ic['funding']['ic_full']}  cum_funding IC {ic['cum_funding']['ic_full']}")
        df = frame(sym)
        rows, wf = {}, {}
        for v in ["baseline", "funding_only", "agree"]:
            rows[v] = evalv(df, make_sig(df, p, v))
            wf[v] = walk_forward(df, v)
        out[sym] = {"ic": ic, "in_sample": rows, "oos_sharpe": wf}
        print(f"  {'variant':<14}{'sharpe':>7}{'total%':>8}{'maxDD%':>8}{'trades':>7}{'win%':>7}{'OOS':>7}")
        for v in ["baseline", "funding_only", "agree"]:
            r = rows[v]
            if not r: print(f"  {v:<14}  (too few)"); continue
            print(f"  {v:<14}{r['sharpe']:>7}{r['total_pct']:>8}{r['maxdd_pct']:>8}{r['trades']:>7}{r['win_pct']:>7}{str(wf[v]):>7}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "funding.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'funding.json'}")


if __name__ == "__main__":
    main()
