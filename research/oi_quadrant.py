"""
Experiment #5 — OI × price-direction QUADRANT factor (the last shot at OI).

Naive OI percentile is dead (§6e discovery scan, Sharpe ~-0.5) — because OI LEVEL
alone has no directional meaning. The quadrant thesis: what matters is ΔOI paired
with price direction, which tells you WHO is doing what:
  * price↑ + OI↑ = new LONGS opening, pushing price up  -> crowding, fade SHORT
  * price↓ + OI↑ = new SHORTS opening, pushing price down -> crowding, fade LONG
  * price↑ + OI↓ = shorts covering (squeeze) -> weak/reversal-prone
  * price↓ + OI↓ = longs capitulating -> weak/reversal-prone
For a mean-reversion fade, the "OI rising into an extreme move" quadrants are the
golden ones: new leverage is crowding into a move that's likely to snap back.

Tested standalone (own merit, >1.2 bar) AND full-period incl 2022 via wf.py.
Direction pinned by thesis, not fit. Whitelist coins.

    python research/oi_quadrant.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib import build_hourly                                    # noqa: E402
from research.wf import evaluate, fmt                                    # noqa: E402
from src.strategy.signal import (FLAT, LONG, SHORT, Params, compute,    # noqa: E402
                                 percentile_rank, position_size,
                                 realized_daily_vol)

COINS = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]
PCT_WIN_D = 45
LOOKBACK_H = 24                              # ΔOI / Δprice measured over 24h
CACHE = ROOT / "data" / "research"


def load_oi(df, sym):
    fac = pd.read_parquet(CACHE / f"{sym}_factors.parquet")
    df = df.copy()
    df["oi"] = fac["sum_open_interest"].reindex(df.index)
    return df


def base_cols(df, p):
    out = df[["open", "high", "low", "close"]].copy()
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)
    out["pct"] = 0.5
    return out


def sig_crowding_fade(df, p):
    """Fade the leverage-crowding quadrants: enter when ΔOI>0 AND price extended,
    against the move. price↑+OI↑ -> SHORT; price↓+OI↑ -> LONG. Sized by how extreme
    the combined crowding (OI surge percentile) is."""
    out = base_cols(df, p)
    d_oi = df["oi"].pct_change(LOOKBACK_H)
    d_px = df["close"].pct_change(LOOKBACK_H)
    oi_surge = percentile_rank(d_oi, PCT_WIN_D * 24)      # how big is the OI build vs history
    t = pd.Series(FLAT, index=out.index, dtype=int)
    strong_oi = oi_surge >= 0.90                          # top-decile OI surge
    t[strong_oi & (d_px > 0)] = SHORT                     # new longs crowding a rally -> fade
    t[strong_oi & (d_px < 0)] = LONG                      # new shorts crowding a selloff -> fade
    t[d_oi.isna() | d_px.isna()] = FLAT
    out["target"] = t
    return out


def sig_reversal_quadrant(df, p):
    """The other pair: OI FALLING during a move = squeeze/capitulation -> reversal.
    price↑+OI↓ (short squeeze topping) -> SHORT; price↓+OI↓ (capitulation bottoming) -> LONG."""
    out = base_cols(df, p)
    d_oi = df["oi"].pct_change(LOOKBACK_H)
    d_px = df["close"].pct_change(LOOKBACK_H)
    px_ext = percentile_rank(df["close"].pct_change(LOOKBACK_H), PCT_WIN_D * 24)
    t = pd.Series(FLAT, index=out.index, dtype=int)
    t[(d_oi < 0) & (px_ext >= 0.90)] = SHORT              # squeeze top -> fade
    t[(d_oi < 0) & (px_ext <= 0.10)] = LONG               # capitulation bottom -> fade
    t[d_oi.isna() | d_px.isna()] = FLAT
    out["target"] = t
    return out


def main():
    p = Params(lookback_hours=PCT_WIN_D * 24)
    print("=" * 96)
    print("OI × price QUADRANT factor — standalone, full-period incl 2022 (bar: own-merit Sharpe>1.2)")
    print("=" * 96)
    for sym in COINS:
        df = load_oi(build_hourly(sym), sym)
        print(f"\n===== {sym} =====")
        # L/S control for orthogonality
        ls = evaluate(df, lambda d: compute(d, p), dict(params=p, exit_mode="time"))
        print(fmt(ls, "L/S control"))
        c1 = evaluate(df, lambda d: sig_crowding_fade(d, p),
                      dict(params=p, exit_mode="time"), ref_ret=ls["_full_daily_ret"])
        print(fmt(c1, "OI-crowding fade") + f"  corr-to-L/S {c1.get('corr_to_ref')}"
              f"  trades {c1['full_2022']['trades']}")
        c2 = evaluate(df, lambda d: sig_reversal_quadrant(d, p),
                      dict(params=p, exit_mode="time"), ref_ret=ls["_full_daily_ret"])
        print(fmt(c2, "OI-reversal quad") + f"  corr-to-L/S {c2.get('corr_to_ref')}"
              f"  trades {c2['full_2022']['trades']}")


if __name__ == "__main__":
    main()
