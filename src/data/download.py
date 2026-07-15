"""
Data pipeline for the Retail Long/Short-Ratio Reversion strategy.

Pulls two public, no-auth datasets from Binance Vision (data.binance.vision):

  1. 1h USDⓈ-M perpetual klines  (price)          -> monthly zip files
  2. Futures metrics             (long/short ratio) -> daily   zip files

The metric we trade on is ``count_long_short_ratio`` == the "Long/Short Ratio
(Accounts)" that the live REST endpoint exposes as ``globalLongShortAccountRatio``.
Metrics files are 5-minute granularity; we resample to hourly (value as of the
close of each hour) so it lines up with the 1h klines.

Everything is cached under data/raw/ so re-runs are cheap. Output is a single
tidy hourly frame written to data/hourly.parquet with columns:

    open, high, low, close, volume, lsr   (indexed by hourly UTC timestamp)

Usage:
    python -m src.data.download --start 2021-10-01 --end 2026-06-30
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/futures/um"
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "hourly.parquet"

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "crypto_test/1.0 (+backtest data loader)"})
    return s


def _download(sess: requests.Session, url: str, dest: Path) -> bool:
    """Download url -> dest (cached). Returns True if the file is available."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    r = sess.get(url, timeout=60)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return True


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d = date.fromordinal(d.toordinal() + 1)


# --------------------------------------------------------------------------- #
# klines (monthly)                                                            #
# --------------------------------------------------------------------------- #
def _fetch_kline_month(sess, symbol, y, m):
    tag = f"{y:04d}-{m:02d}"
    url = f"{BASE}/monthly/klines/{symbol}/1h/{symbol}-1h-{tag}.zip"
    dest = RAW / "klines" / f"{symbol}-1h-{tag}.zip"
    if not _download(sess, url, dest):
        return None
    with zipfile.ZipFile(dest) as z:
        name = z.namelist()[0]
        raw = z.read(name)
    # Some monthly files ship a header row, some don't; sniff the first token.
    first = raw[:20].decode("utf-8", "ignore").lstrip()
    header = 0 if first.startswith("open_time") else None
    df = pd.read_csv(io.BytesIO(raw), header=header, names=KLINE_COLS if header is None else None)
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])
    # open_time is ms; a few dumps use microseconds -> normalise.
    unit = "us" if df["open_time"].iloc[0] > 1e16 else "ms"
    df["ts"] = pd.to_datetime(df["open_time"], unit=unit, utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float)


def load_klines(sess, symbol, start, end):
    frames, misses = [], []
    tasks = list(_months(start, end))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_kline_month, sess, symbol, y, m): (y, m) for y, m in tasks}
        for fut in as_completed(futs):
            y, m = futs[fut]
            df = fut.result()
            if df is None:
                misses.append(f"{y}-{m:02d}")
            else:
                frames.append(df)
    if not frames:
        raise SystemExit("No kline data downloaded — check symbol / date range.")
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    if misses:
        print(f"  klines: {len(misses)} months missing: {sorted(misses)}")
    return out


# --------------------------------------------------------------------------- #
# metrics (daily) -> hourly long/short ratio                                  #
# --------------------------------------------------------------------------- #
def _fetch_metric_day(sess, symbol, d):
    tag = d.strftime("%Y-%m-%d")
    url = f"{BASE}/daily/metrics/{symbol}/{symbol}-metrics-{tag}.zip"
    dest = RAW / "metrics" / f"{symbol}-metrics-{tag}.zip"
    if not _download(sess, url, dest):
        return None
    with zipfile.ZipFile(dest) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name), usecols=["create_time", "count_long_short_ratio"])
    df["ts"] = pd.to_datetime(df["create_time"], utc=True)
    df["lsr"] = pd.to_numeric(df["count_long_short_ratio"], errors="coerce")
    return df.set_index("ts")["lsr"].dropna()


def load_lsr(sess, symbol, start, end):
    series, misses = [], 0
    tasks = list(_days(start, end))
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(_fetch_metric_day, sess, symbol, d): d for d in tasks}
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 250 == 0:
                print(f"  metrics: {done}/{len(tasks)} days fetched")
            s = fut.result()
            if s is None or s.empty:
                misses += 1
            else:
                series.append(s)
    if not series:
        raise SystemExit("No metrics data downloaded — long/short ratio unavailable.")
    lsr = pd.concat(series).sort_index()
    lsr = lsr[~lsr.index.duplicated(keep="first")]
    # 5-min -> hourly: value as of the close of each hour.
    hourly = lsr.resample("1h").last()
    if misses:
        print(f"  metrics: {misses} days missing (skipped)")
    return hourly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2021-10-01", help="incl. 90d warmup before backtest start")
    ap.add_argument("--end", default="2026-06-30")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    sess = _session()

    print(f"[1/2] klines  {args.symbol} 1h  {start}..{end}")
    kl = load_klines(sess, args.symbol, start, end)
    print(f"      -> {len(kl):,} hourly candles  ({kl.index.min()} .. {kl.index.max()})")

    print(f"[2/2] metrics {args.symbol} long/short ratio  {start}..{end}")
    lsr = load_lsr(sess, args.symbol, start, end)
    print(f"      -> {len(lsr):,} hourly ratio points")

    df = kl.join(lsr.rename("lsr"), how="left")
    # Metrics have occasional short gaps; forward-fill up to 3h, no further.
    df["lsr"] = df["lsr"].ffill(limit=3)
    covered = df["lsr"].notna().mean()
    print(f"      merged: {len(df):,} rows, long/short ratio coverage {covered:.1%}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
