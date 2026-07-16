"""
Multi-factor validation — STEP 1 of the confluence idea, per STRATEGY_SOP.md:
check each factor's standalone predictive power (IC/IR) BEFORE combining anything.

The Binance futures metrics dumps carry five position/flow factors we can use
(5-min granularity -> resampled hourly):
  * count_long_short_ratio           RETAIL / global account L/S (current signal)
  * count_toptrader_long_short_ratio top-trader ACCOUNTS  L/S (smart money, by acct)
  * sum_toptrader_long_short_ratio   top-trader POSITIONS L/S (smart money, by size)
  * sum_taker_long_short_vol_ratio   taker buy/sell volume ratio (aggressor flow)
  * sum_open_interest                open interest (level -> we use its % change)

Hypothesis (to be tested, not assumed): retail L/S is the crowd we fade
(=> NEGATIVE IC vs forward return); top-trader L/S is smart money
(=> POSITIVE or flat IC). If so, "retail extreme AND top-trader diverging" is a
well-founded confluence gate.

IC = Spearman rank corr between factor value at t and the forward 72h (3-day,
matches the hold) return. Reported full-sample and as 30-day-block IC mean/IR.
Acceptance (SOP): |IC| > 0.03 useful, > 0.05 strong, IR > 0.5; |IC| > 0.1 = red
flag (overfit / data bug).

    python research/factors.py
"""
from __future__ import annotations

import glob
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                       # noqa: E402

RAW = ROOT / "data" / "raw" / "metrics"
CACHE = ROOT / "data" / "research"
RESULTS = ROOT / "results"
HOLD_H = 72                                                  # 3-day forward return


def spearman(a: pd.Series | np.ndarray, b: pd.Series | np.ndarray) -> float:
    """Spearman rank correlation = Pearson correlation of the ranks."""
    ar = pd.Series(np.asarray(a, dtype=float)).rank().to_numpy()
    br = pd.Series(np.asarray(b, dtype=float)).rank().to_numpy()
    if len(ar) < 3 or ar.std() == 0 or br.std() == 0:
        return float("nan")
    return float(np.corrcoef(ar, br)[0, 1])
FACTORS = ["count_long_short_ratio", "count_toptrader_long_short_ratio",
           "sum_toptrader_long_short_ratio", "sum_taker_long_short_vol_ratio",
           "sum_open_interest"]
LABELS = {"count_long_short_ratio": "retail L/S (accounts)",
          "count_toptrader_long_short_ratio": "top-trader L/S (accounts)",
          "sum_toptrader_long_short_ratio": "top-trader L/S (positions)",
          "sum_taker_long_short_vol_ratio": "taker buy/sell vol ratio",
          "sum_open_interest": "open interest (Δ%)"}


def build_enriched(symbol: str) -> pd.DataFrame:
    """Hourly frame: close + all 5 factors. Cached per symbol."""
    out = CACHE / f"{symbol}_factors.parquet"
    if out.exists():
        return pd.read_parquet(out)

    frames = []
    for z in sorted(glob.glob(str(RAW / f"{symbol}-metrics-*.zip"))):
        try:
            zf = zipfile.ZipFile(z); name = zf.namelist()[0]
            df = pd.read_csv(zf.open(name), usecols=["create_time"] + FACTORS)
            frames.append(df)
        except Exception:                                    # noqa: BLE001
            continue
    m = pd.concat(frames, ignore_index=True)
    m["ts"] = pd.to_datetime(m["create_time"], utc=True)
    m = m.set_index("ts").sort_index()
    for c in FACTORS:
        m[c] = pd.to_numeric(m[c], errors="coerce")
    hourly = m[FACTORS].resample("1h").last()

    # merge close from the OHLCV research cache
    px = build_hourly(symbol)[["close"]]
    df = px.join(hourly, how="left")
    for c in FACTORS:
        df[c] = df[c].ffill(limit=6)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return df


def ic_report(symbol: str) -> dict:
    df = build_enriched(symbol).copy()
    # forward 3-day return (the diagnostic; not a tradeable leak)
    df["fwd"] = df["close"].shift(-HOLD_H) / df["close"] - 1.0
    # OI is a level -> use its % change as the factor
    df["sum_open_interest"] = df["sum_open_interest"].pct_change(HOLD_H)

    rows = []
    for f in FACTORS:
        sub = df[[f, "fwd"]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub) < 2000:
            rows.append({"factor": f, "label": LABELS[f], "coverage": len(sub),
                         "ic": None, "note": "insufficient coverage"})
            continue
        ic_full = spearman(sub[f], sub["fwd"])
        # 30-day (720h) non-overlapping block ICs -> mean / IR / %positive
        block = 720
        ics = []
        for i in range(0, len(sub) - block, block):
            b = sub.iloc[i:i + block]
            if len(b) > 100:
                c = spearman(b[f], b["fwd"])
                if not np.isnan(c):
                    ics.append(c)
        ics = np.array(ics)
        ic_mean = float(ics.mean()) if len(ics) else float("nan")
        ic_std = float(ics.std()) if len(ics) else float("nan")
        rows.append({
            "factor": f, "label": LABELS[f], "coverage": len(sub),
            "ic_full": round(ic_full, 4),
            "ic_mean_30d": round(ic_mean, 4),
            "ir": round(ic_mean / ic_std, 2) if ic_std else None,
            "pos_frac": round(float((ics > 0).mean()), 2) if len(ics) else None,
            "n_blocks": len(ics),
        })
    return {"symbol": symbol, "hold_hours": HOLD_H, "factors": rows}


def verdict(ic_full: float | None) -> str:
    if ic_full is None:
        return "—"
    a = abs(ic_full)
    if a > 0.1:
        return "RED FLAG (>0.1, suspect)"
    if a > 0.05:
        return "strong"
    if a > 0.03:
        return "useful"
    return "weak/none"


def main():
    out = {}
    for sym in ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]:
        r = ic_report(sym)
        out[sym] = r
        print(f"\n===== {sym} — factor IC vs forward {HOLD_H}h return =====")
        print(f"{'factor':<34}{'IC(full)':>10}{'IC 30d':>9}{'IR':>7}{'pos%':>7}  verdict")
        for row in r["factors"]:
            icf = row.get("ic_full")
            print(f"{row['label']:<34}{('%.4f'%icf) if icf is not None else 'n/a':>10}"
                  f"{('%.4f'%row['ic_mean_30d']) if row.get('ic_mean_30d') is not None else '':>9}"
                  f"{str(row.get('ir')):>7}{str(row.get('pos_frac')):>7}  {verdict(icf)}")
        print("  (retail L/S expected NEGATIVE = fade works; top-trader expected "
              "POSITIVE/flat = don't fade smart money)")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "factor_ic.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {RESULTS/'factor_ic.json'}")


if __name__ == "__main__":
    main()
