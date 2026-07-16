"""
Coinbase Premium factor — STEP 1 (IC/IR), per STRATEGY_SOP.md, before any backtest.

Coinbase Premium = how much a coin trades ABOVE/below on Coinbase (US-regulated,
institutional/US-retail SPOT, USD) vs Binance (global perp/retail, USDT). It is a
completely different participant set from our retail-perp L/S factor, so it's the
most orthogonal offline-backtestable candidate we found.

  premium_bp = (coinbase_usd_close / binance_usdt_close - 1) * 10000

Note: this blends true Coinbase-vs-global premium with USDT/USD basis (USDT depegs
show up here too — itself informative). Same approximation CryptoQuant uses.

Steps: fetch Coinbase hourly candles (cached) -> align with Binance -> premium ->
IC/IR vs forward 72h return. If it clears the bar, confluence.py-style backtest next.

    python research/coinbase_premium.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                       # noqa: E402
from research.factors import spearman                       # noqa: E402

CACHE = ROOT / "data" / "research"
RESULTS = ROOT / "results"
HOLD_H = 72
CB = "https://api.exchange.coinbase.com"
PAIRS = {"BTCUSDT": "BTC-USD", "ADAUSDT": "ADA-USD", "DOGEUSDT": "DOGE-USD"}
START, END = "2022-01-01", "2026-06-30"


def fetch_coinbase(pid: str, start: str, end: str) -> pd.Series:
    """Hourly close from Coinbase, paginated backwards (300/req). Cached."""
    out = CACHE / f"{pid.replace('-', '')}_coinbase.parquet"
    if out.exists():
        return pd.read_parquet(out)["close"]
    s = requests.Session(); s.headers.update({"User-Agent": "crypto_test/1.0"})
    t0 = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    rows = {}
    cur = t1
    while cur > t0:
        lo = max(t0, cur - 300 * 3600)
        r = s.get(f"{CB}/products/{pid}/candles",
                  params={"granularity": 3600, "start": lo, "end": cur}, timeout=30)
        if r.status_code != 200:
            time.sleep(1.0); continue
        d = r.json()
        if not isinstance(d, list) or not d:
            break
        for row in d:                      # [time, low, high, open, close, volume]
            rows[int(row[0])] = float(row[4])
        cur = lo
        time.sleep(0.2)
    ser = pd.Series(rows).sort_index()
    ser.index = pd.to_datetime(ser.index, unit="s", utc=True)
    df = ser.to_frame("close")
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return df["close"]


def premium_frame(binance_sym: str) -> pd.DataFrame:
    b = build_hourly(binance_sym)[["close"]].rename(columns={"close": "bin"})
    cb = fetch_coinbase(PAIRS[binance_sym], START, END).rename("cb")
    df = b.join(cb, how="left")
    df["cb"] = df["cb"].ffill(limit=6)
    df["premium_bp"] = (df["cb"] / df["bin"] - 1.0) * 10000.0
    return df


def ic_of(sym: str) -> dict:
    df = premium_frame(sym)
    df["fwd"] = df["bin"].shift(-HOLD_H) / df["bin"] - 1.0
    sub = df[["premium_bp", "fwd"]].replace([np.inf, -np.inf], np.nan).dropna()
    ic_full = spearman(sub["premium_bp"], sub["fwd"])
    block, ics = 720, []
    for i in range(0, len(sub) - block, block):
        b = sub.iloc[i:i + block]
        if len(b) > 100:
            c = spearman(b["premium_bp"], b["fwd"])
            if not np.isnan(c):
                ics.append(c)
    ics = np.array(ics)
    return {"symbol": sym, "coverage": int(len(sub)),
            "premium_mean_bp": round(float(df["premium_bp"].mean()), 1),
            "premium_std_bp": round(float(df["premium_bp"].std()), 1),
            "ic_full": round(float(ic_full), 4),
            "ic_mean_30d": round(float(ics.mean()), 4) if len(ics) else None,
            "ir": round(float(ics.mean() / ics.std()), 2) if len(ics) and ics.std() else None,
            "pos_frac": round(float((ics > 0).mean()), 2) if len(ics) else None}


def main():
    out = []
    for sym in PAIRS:
        r = ic_of(sym)
        out.append(r)
        print(f"{sym:<9} premium {r['premium_mean_bp']:+.1f}±{r['premium_std_bp']:.0f}bp  "
              f"IC(full) {r['ic_full']:+.4f}  IC30d {r['ic_mean_30d']}  IR {r['ir']}  "
              f"pos% {r['pos_frac']}  (cov {r['coverage']})")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "coinbase_premium_ic.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"saved -> {RESULTS/'coinbase_premium_ic.json'}")
    print("(premium>0 = US institutions bid spot; expect POSITIVE IC if it leads price up,"
          " or use divergence with retail L/S)")


if __name__ == "__main__":
    main()
