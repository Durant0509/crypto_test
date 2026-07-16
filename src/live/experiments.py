"""
Multi-experiment forward paper-trading — runs several INDEPENDENT strategies side
by side, each on its own virtual 1000 USDT account. Separate from the original
single-BTC bot (paper.py defaults), which keeps running untouched.

The three experiments here are the top-3 coins by out-of-sample walk-forward
Sharpe (see results/walk_forward.json), each with the VALIDATED tuned config
(45-day lookback) and its own recommended max leverage (from the leverage-safety
analysis). Coins run separately — no portfolio mixing.

Each experiment keeps:
  * its own ledger  -> paper_state/exp_<name>.json
  * its own price/ratio store -> data/live_<symbol>.parquet
All display payloads are bundled into docs/live_experiments.js (window.EXPERIMENTS)
so the dashboard can show them as clearly-labelled cards.

    python scripts/paper_tick_experiments.py
"""
from __future__ import annotations

import json
from pathlib import Path

from ..strategy.signal import Params
from .paper import tick

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "paper_state"
STORE_DIR = ROOT / "data"
OUT_JS = ROOT / "docs" / "live_experiments.js"

START_EQUITY = 1000.0
# Tuned + validated 45d lookback. Two exit modes run as an A/B:
#   time      = fixed 3-day exit (original)
#   normalize = exit when L/S pct back in 40-60% neutral band, 5-day cap
#               (walk-forward-validated better for BTC/ADA; NOT for DOGE)
TUNED = dict(lookback_days=45, upper=0.90, lower=0.10,
             vol_target=0.025, smin=0.25, smax=3.0)
NEUTRAL_LO, NEUTRAL_HI, MIN_HOLD_H = 0.40, 0.60, 24
EXIT_LABEL = {"time": "固定3天", "normalize": "正規化出場"}


def _params(exit_mode: str) -> Params:
    hold_days = 3 if exit_mode == "time" else 5      # normalize uses 5d as the max cap
    return Params(
        lookback_hours=TUNED["lookback_days"] * 24,
        upper_pct=TUNED["upper"], lower_pct=TUNED["lower"],
        hold_hours=hold_days * 24,
        vol_window_hours=72,
        target_daily_vol=TUNED["vol_target"], size_min=TUNED["smin"], size_max=TUNED["smax"],
    )


# name, coin, symbol, out-of-sample Sharpe, recommended max leverage, exit mode.
# A/B: the same coin runs BOTH fixed-3d and normalize-exit side by side (BTC/ADA).
# DOGE only fixed-3d (normalize was rejected for it out-of-sample).
EXPERIMENTS = [
    {"name": "ada-tuned",       "coin": "ADA",  "symbol": "ADAUSDT",  "oos_sharpe": 1.68, "leverage": 2, "exit": "time"},
    {"name": "btc-tuned",       "coin": "BTC",  "symbol": "BTCUSDT",  "oos_sharpe": 1.58, "leverage": 3, "exit": "time"},
    {"name": "doge-tuned",      "coin": "DOGE", "symbol": "DOGEUSDT", "oos_sharpe": 1.15, "leverage": 2, "exit": "time"},
    {"name": "ada-tuned-norm",  "coin": "ADA",  "symbol": "ADAUSDT",  "oos_sharpe": 1.82, "leverage": 2, "exit": "normalize"},
    {"name": "btc-tuned-norm",  "coin": "BTC",  "symbol": "BTCUSDT",  "oos_sharpe": 1.73, "leverage": 3, "exit": "normalize"},
]


def _meta(exp: dict) -> dict:
    """The parameter-category descriptor shown on the dashboard card."""
    el = EXIT_LABEL[exp["exit"]]
    return {
        "name": exp["name"], "coin": exp["coin"], "symbol": exp["symbol"],
        "variant": "調優 tuned",
        "exit_mode": exp["exit"], "exit_label": el,
        "category": f"{exp['coin']} · 45天 · {el} · ≤{exp['leverage']}x",
        "lookback_days": TUNED["lookback_days"],
        "upper_pct": TUNED["upper"], "lower_pct": TUNED["lower"],
        "hold_days": 3 if exp["exit"] == "time" else 5,
        "leverage": exp["leverage"],
        "max_notional": START_EQUITY * exp["leverage"],
        "start_equity": START_EQUITY,
        "oos_sharpe": exp["oos_sharpe"],
    }


def tick_all() -> list[dict]:
    payloads = []
    for exp in EXPERIMENTS:
        ledger_path = STATE / f"exp_{exp['name']}.json"
        store_path = STORE_DIR / f"live_{exp['symbol']}.parquet"
        max_notional = START_EQUITY * exp["leverage"]
        try:
            _led, payload = tick(
                params=_params(exp["exit"]), symbol=exp["symbol"],
                start_equity=START_EQUITY, max_notional=max_notional,
                ledger_path=ledger_path, store_path=store_path,
                write_js=False, return_payload=True,
                exit_mode=exp["exit"], neutral_lo=NEUTRAL_LO, neutral_hi=NEUTRAL_HI,
                min_hold_hours=MIN_HOLD_H,
            )
            payload["meta"] = _meta(exp)
            payloads.append(payload)
            pos = payload.get("position")
            print(f"[{exp['name']}] equity={payload['equity']:.2f} "
                  f"signal={payload['signal']['target']} "
                  f"pos={pos['side'] if pos else 'flat'} trades={payload['n_trades']}")
        except SystemExit as e:                       # not enough history yet
            payloads.append({"meta": _meta(exp), "error": str(e)})
            print(f"[{exp['name']}] skipped: {e}")

    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text("window.EXPERIMENTS = " + json.dumps(payloads, default=str) + ";\n")
    return payloads
