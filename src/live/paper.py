"""
Forward paper-trading engine — a virtual 1000 USDT account, starting NOW.

Unlike the backtest (which replays 5 years of history), this runs one tick at a
time going forward: each hour it reads the REAL live signal and fills a VIRTUAL
order at the REAL current mark price. Every entry/exit is timestamped and
persisted, so after a month you have a genuine out-of-sample paper track record
to inspect before wiring up a real account.

  * Virtual balance starts at `start_equity` (default 1000 USDT).
  * Position notional = equity x inverse-vol multiplier (0.25-3x leverage),
    capped at `max_notional`.
  * Fills at the live mark price; taker fee + slippage deducted per side.
  * 3-day time exit, NO stop-loss / take-profit (by design — see README).
  * Ledger persisted to paper_state/ledger.json; display data to docs/live.js.

Same signal code as the backtest and the testnet bot, so behaviour is identical.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..backtest.engine import Costs
from ..strategy.signal import FLAT, LONG, SHORT, Params, compute
from .binance_client import BinanceFutures
from .history import update_store

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "paper_state" / "ledger.json"
STORE = ROOT / "data" / "live_hourly.parquet"
LIVE_JS = ROOT / "docs" / "live.js"

SYMBOL = "BTCUSDT"
START_EQUITY = 1000.0
MAX_NOTIONAL = 3000.0          # hard cap (3x on the initial 1000)


def _now_hour() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").floor("1h")


def _load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {
        "start_time": pd.Timestamp.now(tz="UTC").isoformat(),
        "start_equity": START_EQUITY,
        "equity": START_EQUITY,
        "realized_pnl": 0.0,
        "last_acted_candle": None,
        "position": None,
        "trades": [],
        "equity_curve": [],
        "halted": False,
    }


def _save_ledger(led: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, indent=2, default=str))


def _ref_levels(side: str, entry_px: float, size_mult: float) -> dict:
    """Price levels where UNREALISED loss would hit -10% / -20% of the position's
    equity-at-risk. Informational only — nothing triggers here (no stop)."""
    def px_at(loss):  # loss as positive fraction of equity slice
        move = loss / size_mult
        return entry_px * (1 - move) if side == "LONG" else entry_px * (1 + move)
    return {"minus10": round(px_at(0.10), 1), "minus20": round(px_at(0.20), 1)}


def tick(params: Params = Params(), symbol: str = SYMBOL,
         start_equity: float = START_EQUITY, max_notional: float = MAX_NOTIONAL,
         costs: Costs = Costs()) -> dict:
    client = BinanceFutures()                       # public data only, no keys
    df = update_store(client, symbol, STORE)
    closed = df[df.index < _now_hour()]
    if len(closed) < params.lookback_hours:
        raise SystemExit(f"not enough history ({len(closed)}/{params.lookback_hours}h)")

    sig = compute(closed, params)
    bar = sig.iloc[-1]
    candle_ts = sig.index[-1]
    candle_iso = candle_ts.isoformat()
    target = int(bar["target"])
    tname = {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}[target]
    price = client.mark_price(symbol)               # live fill price
    cps = costs.per_side

    led = _load_ledger()
    pos = led["position"]
    equity = led["equity"]
    acted = False

    # --- manage an open position: mark-to-market, then 3-day time exit ------
    unrealized = 0.0
    if pos:
        d = 1 if pos["side"] == "LONG" else -1
        unrealized = pos["notional"] * d * (price / pos["entry_px"] - 1.0)
        entry_candle = pd.Timestamp(pos["entry_candle"])
        age_h = (candle_ts - entry_candle) / pd.Timedelta(hours=1)
        if age_h >= params.hold_hours and led["last_acted_candle"] != candle_iso:
            gross = d * (price / pos["entry_px"] - 1.0)
            pnl = pos["notional"] * gross - pos["notional"] * cps   # exit-leg cost
            equity += pnl
            led["realized_pnl"] += pnl
            led["trades"].append({
                "entry_time": pos["entry_time"], "exit_time": pd.Timestamp.now(tz="UTC").isoformat(),
                "side": pos["side"], "entry_px": pos["entry_px"], "exit_px": round(price, 1),
                "qty": pos["qty"], "notional": pos["notional"], "size_mult": pos["size_mult"],
                "gross_return": round(gross * 100, 3), "pnl": round(pnl, 2),
                "equity_after": round(equity, 2), "win": pnl > 0,
            })
            pos = None
            led["position"] = None
            led["last_acted_candle"] = candle_iso
            led["equity"] = equity
            unrealized = 0.0
            acted = True

    # --- entry: flat, signal fires, not already acted this candle ----------
    if pos is None and not acted and not led["halted"] \
            and target != FLAT and led["last_acted_candle"] != candle_iso:
        size_mult = float(bar["size"])
        notional = min(equity * size_mult, max_notional)
        qty = notional / price
        entry_cost = notional * cps
        equity -= entry_cost                        # entry-leg cost felt now
        led["equity"] = equity
        led["realized_pnl"] -= entry_cost
        exit_candle = (candle_ts + pd.Timedelta(hours=params.hold_hours)).isoformat()
        pos = {
            "side": tname, "entry_time": pd.Timestamp.now(tz="UTC").isoformat(),
            "entry_candle": candle_iso, "entry_px": round(price, 1),
            "qty": round(qty, 6), "notional": round(notional, 2), "size_mult": round(size_mult, 2),
            "scheduled_exit_candle": exit_candle,
            "ref_levels": _ref_levels(tname, price, size_mult),
        }
        led["position"] = pos
        led["last_acted_candle"] = candle_iso
        acted = True

    # --- snapshot equity every tick ----------------------------------------
    eq_now = round(led["equity"] + unrealized, 2)
    led["equity_curve"].append({"t": candle_iso, "eq": eq_now, "px": round(price, 1)})
    # keep it bounded (a year of hourly points is plenty)
    led["equity_curve"] = led["equity_curve"][-24 * 400:]

    _save_ledger(led)
    _write_live_js(led, sig, bar, candle_ts, price, unrealized)
    return led


def _write_live_js(led, sig, bar, candle_ts, price, unrealized) -> None:
    p = led["position"]
    now = pd.Timestamp.now(tz="UTC")
    pos_out = None
    if p:
        exit_c = pd.Timestamp(p["scheduled_exit_candle"])
        pos_out = {
            **p,
            "unrealized": round(unrealized, 2),
            "unrealized_pct": round(unrealized / led["start_equity"] * 100, 2),
            "age_h": round((candle_ts - pd.Timestamp(p["entry_candle"])) / pd.Timedelta(hours=1)),
            "hours_left": max(0, round((exit_c - now) / pd.Timedelta(hours=1))),
        }
    trades = led["trades"]
    wins = sum(1 for t in trades if t["win"])
    tgt = int(bar["target"])
    payload = {
        "updated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "start_time": led["start_time"][:16].replace("T", " "),
        "start_equity": led["start_equity"],
        "equity": round(led["equity"] + unrealized, 2),
        "realized_equity": round(led["equity"], 2),
        "unrealized": round(unrealized, 2),
        "return_pct": round((led["equity"] + unrealized) / led["start_equity"] * 100 - 100, 2),
        "signal": {
            "candle": candle_ts.strftime("%Y-%m-%d %H:%M UTC"),
            "lsr": round(float(bar["lsr"]), 3),
            "pct": round(float(bar["pct"]) * 100, 1),
            "size": round(float(bar["size"]), 2),
            "target": {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}[tgt],
            "price": round(price, 1),
        },
        "position": pos_out,
        "halted": led["halted"],
        "n_trades": len(trades),
        "win_rate": round(wins / len(trades) * 100, 1) if trades else None,
        "trades": trades[-100:][::-1],           # most recent first
        "equity_curve": led["equity_curve"],
    }
    LIVE_JS.parent.mkdir(parents=True, exist_ok=True)
    LIVE_JS.write_text("window.LIVE = " + json.dumps(payload, default=str) + ";\n")
