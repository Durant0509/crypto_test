"""
Rolling hourly (close, lsr) store for the live bot.

The 90-day percentile needs ~2160 hourly ratio points, but the public API only
returns ~500 (≈20 days) per call. So the bot keeps its own store:

  * On first run it BOOTSTRAPS the trailing ~100 days — from the pre-built
    data/hourly.parquet if present, otherwise straight from Binance Vision.
  * Every run it TOPS UP with the latest klines + ratio from the live API and
    appends, dedups, and persists.

This is exactly the "record what the strategy sees, every hour" loop the spec
describes, and it's what accumulates the genuine out-of-sample track record.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ..data.download import _session, load_klines, load_lsr
from .binance_client import BinanceFutures

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "hourly.parquet"


def _bootstrap(symbol: str, days: int = 110) -> pd.DataFrame:
    if SEED.exists():
        df = pd.read_parquet(SEED)
        return df[["close", "lsr"]].tail(days * 24).copy()
    sess = _session()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    kl = load_klines(sess, symbol, start, end)
    lsr = load_lsr(sess, symbol, start, end)
    df = kl[["close"]].join(lsr.rename("lsr"), how="left")
    df["lsr"] = df["lsr"].ffill(limit=3)
    return df


def _live_recent(client: BinanceFutures, symbol: str) -> pd.DataFrame:
    kl = client.klines(symbol, "1h", 500)
    k = pd.DataFrame(kl, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "qv", "n", "tbv", "tbqv", "ig"])
    k["ts"] = pd.to_datetime(k["open_time"], unit="ms", utc=True)
    k = k.set_index("ts")[["close"]].astype(float)

    r = pd.DataFrame(client.long_short_ratio(symbol, "1h", 500))
    r["ts"] = pd.to_datetime(r["timestamp"], unit="ms", utc=True).dt.floor("1h")
    r["lsr"] = pd.to_numeric(r["longShortRatio"], errors="coerce")
    r = r.set_index("ts")["lsr"]
    return k.join(r.rename("lsr"), how="left")


def update_store(client: BinanceFutures, symbol: str, store_path: Path) -> pd.DataFrame:
    """Load or bootstrap the store, top it up from the live API, persist, return."""
    if store_path.exists():
        store = pd.read_parquet(store_path)
    else:
        store = _bootstrap(symbol)

    recent = _live_recent(client, symbol)
    merged = pd.concat([store, recent])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged["lsr"] = merged["lsr"].ffill(limit=3)
    # keep it bounded: 200 days is plenty for a 90-day window
    merged = merged.tail(200 * 24)

    store_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(store_path)
    return merged
