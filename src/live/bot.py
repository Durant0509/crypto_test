"""
Live trading bot for the Retail Long/Short-Ratio Reversion strategy.

Runs on Binance USDⓈ-M **testnet** (paper money). Designed to be invoked once
per hour (e.g. from cron) via ``--once``; it evaluates the latest closed hourly
candle and takes at most ONE action per candle.

Safety features (mirroring the spec's risk controls):
  * dry_run           - compute & log only, never send an order (config default).
  * idempotency       - a candle timestamp is recorded after acting; the same
                        candle can never trigger a second order.
  * notional hard cap - position notional is clipped to max_notional.
  * emergency flatten - ``--flatten`` closes everything and writes a HALT file;
                        while HALT exists the bot refuses to open new positions.
  * exchange reconcile- the live position is read back from the exchange, not
                        trusted blindly from local state.

The trading decision uses the SAME src.strategy.signal code as the backtest, so
live and simulated behaviour cannot diverge.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from ..strategy.signal import FLAT, LONG, SHORT, Params, compute
from .binance_client import TESTNET, BinanceFutures
from .history import update_store

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "bot_state.json"
STORE = ROOT / "data" / "live_hourly.parquet"
HALT = ROOT / "data" / "HALT"
LOG = ROOT / "data" / "bot.log"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text())
    return cfg


def params_from_cfg(cfg: dict) -> Params:
    s = cfg.get("strategy", {})
    return Params(
        lookback_hours=int(s.get("lookback_days", 90)) * 24,
        upper_pct=float(s.get("upper_pct", 0.90)),
        lower_pct=float(s.get("lower_pct", 0.10)),
        hold_hours=int(s.get("hold_days", 3)) * 24,
        vol_window_hours=int(s.get("vol_window_hours", 72)),
        target_daily_vol=float(s.get("target_daily_vol", 0.025)),
        size_min=float(s.get("size_min", 0.25)),
        size_max=float(s.get("size_max", 3.0)),
    )


def read_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, default=str))


def round_step(qty: float, step: float) -> float:
    if step <= 0:
        return round(qty, 3)
    return math.floor(qty / step) * step


def flatten(client: BinanceFutures, symbol: str, dry_run: bool) -> None:
    amt = client.position_amt(symbol) if client.api_key else 0.0
    if abs(amt) < 1e-12:
        log("flatten: no open position")
    else:
        side = "SELL" if amt > 0 else "BUY"
        log(f"flatten: closing {amt} {symbol} via {side} reduceOnly (dry_run={dry_run})")
        if not dry_run:
            client.market_order(symbol, side, abs(amt), reduce_only=True)
    HALT.parent.mkdir(parents=True, exist_ok=True)
    HALT.write_text(f"halted at {datetime.now(timezone.utc).isoformat()}\n")
    st = read_state()
    st["position"] = None
    write_state(st)
    log("flatten: HALT file written — bot will not open new positions until it is removed")


def evaluate_once(cfg: dict, dry_run: bool) -> None:
    symbol = cfg["symbol"]
    p = params_from_cfg(cfg)
    client = BinanceFutures(cfg.get("api_key", ""), cfg.get("api_secret", ""),
                            base_url=cfg.get("base_url", TESTNET))

    # 1. refresh rolling history and compute the signal on the last CLOSED bar
    df = update_store(client, symbol, STORE)
    now_hour = pd.Timestamp.now(tz="UTC").floor("1h")
    closed = df[df.index < now_hour]
    if len(closed) < p.lookback_hours:
        log(f"not enough history yet ({len(closed)}/{p.lookback_hours}h) — skipping")
        return
    sig = compute(closed, p)
    bar = sig.iloc[-1]
    candle_ts = sig.index[-1].isoformat()
    target = int(bar["target"])
    tname = {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}[target]
    log(f"signal: candle={candle_ts} lsr={bar['lsr']:.3f} pct={bar['pct']*100:.1f}% "
        f"size={bar['size']:.2f} -> target={tname}")

    state = read_state()

    # 2. reconcile with the exchange (needs API keys; dry-run w/o keys assumes flat)
    has_keys = bool(cfg.get("api_key"))
    amt = client.position_amt(symbol) if has_keys else 0.0
    if not has_keys:
        log("no API keys configured — running signal-only (would-be orders are logged, none sent)")
    cur_side = LONG if amt > 1e-12 else SHORT if amt < -1e-12 else FLAT

    # 3. manage an open position: 3-day time exit
    pos = state.get("position")
    if cur_side != FLAT and pos:
        entry_candle = pd.Timestamp(pos["entry_candle"])
        age_h = (sig.index[-1] - entry_candle) / pd.Timedelta(hours=1)
        if age_h >= p.hold_hours:
            side = "SELL" if amt > 0 else "BUY"
            log(f"exit: hold {age_h:.0f}h >= {p.hold_hours}h -> close {amt} via {side} (dry_run={dry_run})")
            if not dry_run:
                client.market_order(symbol, side, abs(amt), reduce_only=True)
            state["position"] = None
            state["last_acted_candle"] = candle_ts
            write_state(state)
            return
        else:
            log(f"holding: {tname} position age {age_h:.0f}h / {p.hold_hours}h — no action")
            return

    # 4. idempotency: never act twice on the same candle
    if state.get("last_acted_candle") == candle_ts:
        log("idempotent: already acted on this candle — skipping")
        return

    # 5. entry
    if HALT.exists():
        log("HALT present — not opening new positions")
        return
    if cur_side != FLAT:
        log(f"exchange shows an unexpected {['','LONG','SHORT'][cur_side]} position with no local record — skipping (reconcile manually)")
        return
    if target == FLAT:
        log("no entry signal — flat")
        return

    price = client.mark_price(symbol)
    notional = min(cfg["base_notional"] * float(bar["size"]), cfg["max_notional"])
    step = client.step_size(symbol)
    qty = round_step(notional / price, step)
    if qty <= 0:
        log("computed qty <= 0 — skipping")
        return
    side = "BUY" if target == LONG else "SELL"
    log(f"entry: {tname} {qty} {symbol} @~{price:.1f} notional~{qty*price:.0f} (cap {cfg['max_notional']}) dry_run={dry_run}")
    if not dry_run:
        client.market_order(symbol, side, qty)
    state["position"] = {
        "side": tname, "qty": qty, "entry_px": price,
        "entry_candle": candle_ts,
        "entry_time": datetime.now(timezone.utc).isoformat(),
    }
    state["last_acted_candle"] = candle_ts
    write_state(state)


def main():
    ap = argparse.ArgumentParser(description="Retail L/S Reversion — testnet bot")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--once", action="store_true", help="evaluate one candle and exit (cron-friendly)")
    ap.add_argument("--flatten", action="store_true", help="EMERGENCY: close all and halt")
    ap.add_argument("--dry-run", action="store_true", help="force dry-run regardless of config")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        raise SystemExit(f"config not found: {cfg_path} (copy config.example.yaml -> config.yaml)")
    cfg = load_config(cfg_path)
    dry_run = bool(cfg.get("dry_run", True)) or args.dry_run

    if args.flatten:
        client = BinanceFutures(cfg.get("api_key", ""), cfg.get("api_secret", ""),
                                base_url=cfg.get("base_url", TESTNET))
        flatten(client, cfg["symbol"], dry_run)
        return

    evaluate_once(cfg, dry_run)


if __name__ == "__main__":
    main()
