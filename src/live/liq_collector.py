"""
Liquidation collector — self-grown history for the liquidation-cascade factor.

WHY THIS EXISTS: Binance's liquidation REST endpoints are dead (allForceOrders =
"out of maintenance"; forceOrders needs a private key + only returns your own).
The ONLY free public source of liquidations is the WebSocket forceOrder stream,
which has NO history — it only pushes events from the moment you connect. So to
ever backtest a liquidation factor we must start collecting NOW and let months
accumulate. (Paid Coinglass = buying 2y of this history someone already stored.)

WHAT IT DOES:
  * Subscribes to the market-wide stream  wss://fstream.binance.com/ws/!forceOrder@arr
    (ALL symbols on one connection — cheap, and we may test coins beyond BTC/ETH/BNB).
  * Auto-reconnects (a months-long collector WILL drop; disconnect is normal).
  * Buffers events in memory, flushes to a per-UTC-day parquet every FLUSH_SECONDS
    (crash loses at most one flush window). One file per day mirrors the
    data.binance.vision dump layout, so the backtest can read it by date later.
  * Stores RAW event fields only (symbol, side, price, qty, event_time). No
    derived features — feature engineering belongs in the backtest, not here.

Each forceOrder event 'o' block:
  s=symbol, S=side (SELL = a LONG got liquidated; BUY = a SHORT got liquidated),
  p=price, q=qty, T=trade time (ms). Note Binance THROTTLES to 1 push/sec per
  symbol+side, so extreme bursts under-report true volume — trend is still valid.

Run (foreground, or via nohup / a launchd/pm2 service for the long haul):
    python -m src.live.liq_collector
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import websocket   # websocket-client

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "liquidations"
WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
FLUSH_SECONDS = 3600          # flush to disk hourly
PING_INTERVAL = 180           # keepalive ping
LOG = ROOT / "data" / "liq_collector.log"

_buffer: list[dict] = []
_lock = threading.Lock()
_stop = threading.Event()


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse(msg: str) -> dict | None:
    """Extract the raw fields from a forceOrder event. Returns None if not one."""
    try:
        d = json.loads(msg)
        o = d.get("o") or d.get("data", {}).get("o")
        if not o:
            return None
        return {
            "event_time": int(o["T"]),                       # ms
            "symbol": o["s"],
            "side": o["S"],                                  # SELL=long liquidated, BUY=short liquidated
            "price": float(o["p"]),
            "qty": float(o["q"]),
            "avg_price": float(o.get("ap", o["p"])),
            "notional": float(o["p"]) * float(o["q"]),
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _flush() -> None:
    """Append the in-memory buffer to today's UTC parquet, then clear it."""
    global _buffer
    with _lock:
        if not _buffer:
            return
        rows, _buffer = _buffer, []
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["event_time"], unit="ms", utc=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"liq-{day}.parquet"
    if path.exists():                                        # append to today's file
        old = pd.read_parquet(path)
        df = pd.concat([old, df], ignore_index=True)
    df.to_parquet(path, index=False)
    _log(f"flushed {len(rows)} events (+) -> {path.name} (total {len(df)})")


def _flusher() -> None:
    """Background thread: periodic flush so a crash loses at most one window."""
    while not _stop.wait(FLUSH_SECONDS):
        try:
            _flush()
        except Exception as e:                               # never let the flusher die
            _log(f"flush error: {e}")


def _on_message(ws, message):
    ev = _parse(message)
    if ev:
        with _lock:
            _buffer.append(ev)


def _on_error(ws, error):
    _log(f"ws error: {error}")


def _on_close(ws, code, msg):
    _log(f"ws closed code={code} msg={msg}")


def _on_open(ws):
    _log("ws connected -> !forceOrder@arr (all symbols)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"liq_collector starting; flushing every {FLUSH_SECONDS}s to {OUT_DIR}")

    def _graceful(*_):
        _log("stopping; final flush")
        _stop.set()
        try:
            _flush()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _graceful)
    signal.signal(signal.SIGTERM, _graceful)

    t = threading.Thread(target=_flusher, daemon=True)
    t.start()

    backoff = 1
    while not _stop.is_set():
        try:
            ws = websocket.WebSocketApp(
                WS_URL, on_message=_on_message, on_error=_on_error,
                on_close=_on_close, on_open=_on_open,
            )
            ws.run_forever(ping_interval=PING_INTERVAL, ping_timeout=10)
        except Exception as e:
            _log(f"run_forever crashed: {e}")
        if _stop.is_set():
            break
        _flush()                                             # save whatever we have before reconnecting
        _log(f"reconnecting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)                       # exponential backoff, cap 60s


if __name__ == "__main__":
    main()
