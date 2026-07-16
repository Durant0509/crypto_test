"""Shared helpers for research scripts (multi-coin, parameter sweeps)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.download import _session, load_klines, load_lsr  # noqa: E402

CACHE = ROOT / "data" / "research"


def build_hourly(symbol: str, start: str = "2021-10-01", end: str = "2026-06-30") -> pd.DataFrame:
    """Download (cached) + merge klines and long/short ratio into an hourly frame
    with columns open/high/low/close/volume/lsr. One parquet per symbol."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{symbol}_hourly.parquet"
    if out.exists():
        return pd.read_parquet(out)

    sess = _session()
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    kl = load_klines(sess, symbol, s, e)
    lsr = load_lsr(sess, symbol, s, e)
    df = kl.join(lsr.rename("lsr"), how="left")
    df["lsr"] = df["lsr"].ffill(limit=6)
    df.to_parquet(out)
    return df
