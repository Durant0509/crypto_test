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


def _px(x: float) -> float:
    """Round a price to ~6 significant figures. Critical for sub-dollar coins:
    a flat round(x, 1) turns DOGE $0.0734 into $0.1 (a ~36% P&L error). Adaptive
    precision keeps ADA/DOGE exact while staying tidy for BTC."""
    ax = abs(x)
    if ax >= 1000:
        return round(x, 2)
    if ax >= 1:
        return round(x, 4)
    if ax >= 0.01:
        return round(x, 6)
    return round(x, 8)


def fmt8(ts) -> str:
    """Display a timestamp as UTC+8 (Taipei) wall clock 'YYYY-MM-DD HH:MM'.

    Ledger/internal timestamps stay in UTC; only the dashboard payload is
    localised. Taiwan is a fixed UTC+8 (no DST), so a plain +8h shift is exact.
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return (t.tz_convert("UTC").tz_localize(None) + pd.Timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _load_ledger(ledger_path: Path = LEDGER, start_equity: float = START_EQUITY) -> dict:
    if ledger_path.exists():
        return json.loads(ledger_path.read_text())
    return {
        "start_time": pd.Timestamp.now(tz="UTC").isoformat(),
        "start_equity": start_equity,
        "equity": start_equity,
        "realized_pnl": 0.0,
        "last_acted_candle": None,
        "position": None,
        "trades": [],
        "equity_curve": [],
        "halted": False,
    }


def _save_ledger(led: dict, ledger_path: Path = LEDGER) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(led, indent=2, default=str))


def _ref_levels(side: str, entry_px: float, size_mult: float) -> dict:
    """Price levels where UNREALISED loss would hit -10% / -20% of the position's
    equity-at-risk. Informational only — nothing triggers here (no stop)."""
    def px_at(loss):  # loss as positive fraction of equity slice
        move = loss / size_mult
        return entry_px * (1 - move) if side == "LONG" else entry_px * (1 + move)
    return {"minus10": _px(px_at(0.10)), "minus20": _px(px_at(0.20))}


def tick(params: Params = Params(), symbol: str = SYMBOL,
         start_equity: float = START_EQUITY, max_notional: float = MAX_NOTIONAL,
         costs: Costs = Costs(),
         ledger_path: Path = LEDGER, store_path: Path = STORE,
         write_js: bool = True, live_js_path: Path = LIVE_JS, live_var: str = "LIVE",
         return_payload: bool = False):
    """Run one forward paper-trading tick.

    Defaults reproduce the ORIGINAL single-BTC bot exactly (ledger.json + live.js
    window.LIVE). Multi-experiment callers pass their own ledger_path/store_path,
    set write_js=False, and use return_payload=True to collect the display dict.
    """
    client = BinanceFutures()                       # public data only, no keys
    df = update_store(client, symbol, store_path)
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

    led = _load_ledger(ledger_path, start_equity)
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
                "side": pos["side"], "entry_px": pos["entry_px"], "exit_px": _px(price),
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
            "entry_candle": candle_iso, "entry_px": _px(price),
            "qty": round(qty, 6), "notional": round(notional, 2), "size_mult": round(size_mult, 2),
            "scheduled_exit_candle": exit_candle,
            "ref_levels": _ref_levels(tname, price, size_mult),
        }
        led["position"] = pos
        led["last_acted_candle"] = candle_iso
        acted = True

    # --- snapshot equity every tick ----------------------------------------
    eq_now = round(led["equity"] + unrealized, 2)
    led["equity_curve"].append({"t": candle_iso, "eq": eq_now, "px": _px(price)})
    # keep it bounded (a year of hourly points is plenty)
    led["equity_curve"] = led["equity_curve"][-24 * 400:]

    _save_ledger(led, ledger_path)
    payload = _build_payload(led, sig, bar, candle_ts, price, unrealized)
    if write_js:
        live_js_path.parent.mkdir(parents=True, exist_ok=True)
        live_js_path.write_text(f"window.{live_var} = " + json.dumps(payload, default=str) + ";\n")
    return (led, payload) if return_payload else led


def _build_payload(led, sig, bar, candle_ts, price, unrealized) -> dict:
    p = led["position"]
    now = pd.Timestamp.now(tz="UTC")
    pos_out = None
    if p:
        exit_c = pd.Timestamp(p["scheduled_exit_candle"])
        pos_out = {
            **p,
            "entry_time": fmt8(p["entry_time"]),                  # display in UTC+8
            "scheduled_exit_candle": fmt8(p["scheduled_exit_candle"]),
            "unrealized": round(unrealized, 2),
            "unrealized_pct": round(unrealized / led["start_equity"] * 100, 2),
            "age_h": round((candle_ts - pd.Timestamp(p["entry_candle"])) / pd.Timedelta(hours=1)),
            "hours_left": max(0, round((exit_c - now) / pd.Timedelta(hours=1))),
        }
    trades = led["trades"]
    wins = sum(1 for t in trades if t["win"])
    tgt = int(bar["target"])
    # localise trade + equity-curve timestamps to UTC+8 for display
    trades_out = []
    for t in trades[-100:][::-1]:
        trades_out.append({**t, "entry_time": fmt8(t["entry_time"]), "exit_time": fmt8(t["exit_time"])})
    equity_out = [{**e, "t": fmt8(e["t"])} for e in led["equity_curve"]]
    payload = {
        "updated": fmt8(now) + " UTC+8",
        "start_time": fmt8(led["start_time"]),
        "start_equity": led["start_equity"],
        "equity": round(led["equity"] + unrealized, 2),
        "realized_equity": round(led["equity"], 2),
        "unrealized": round(unrealized, 2),
        "return_pct": round((led["equity"] + unrealized) / led["start_equity"] * 100 - 100, 2),
        "signal": {
            "candle": fmt8(candle_ts) + " UTC+8",
            "lsr": round(float(bar["lsr"]), 3),
            "pct": round(float(bar["pct"]) * 100, 1),
            "size": round(float(bar["size"]), 2),
            "target": {LONG: "LONG", SHORT: "SHORT", FLAT: "FLAT"}[tgt],
            "price": _px(price),
        },
        "position": pos_out,
        "halted": led["halted"],
        "n_trades": len(trades),
        "win_rate": round(wins / len(trades) * 100, 1) if trades else None,
        "trades": trades_out,                    # most recent first, UTC+8
        "equity_curve": equity_out,
    }
    return payload
