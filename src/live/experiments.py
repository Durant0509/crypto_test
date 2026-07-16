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
# Tuned + validated config (45d lookback held up out-of-sample for BTC/ADA/DOGE).
TUNED = dict(lookback_days=45, upper=0.90, lower=0.10, hold_days=3,
             vol_target=0.025, smin=0.25, smax=3.0)


def _params(cfg=TUNED) -> Params:
    return Params(
        lookback_hours=cfg["lookback_days"] * 24,
        upper_pct=cfg["upper"], lower_pct=cfg["lower"],
        hold_hours=cfg["hold_days"] * 24,
        vol_window_hours=72,
        target_daily_vol=cfg["vol_target"], size_min=cfg["smin"], size_max=cfg["smax"],
    )


# name, coin, symbol, out-of-sample Sharpe, recommended max leverage
EXPERIMENTS = [
    {"name": "ada-tuned", "coin": "ADA", "symbol": "ADAUSDT", "oos_sharpe": 1.68, "leverage": 2},
    {"name": "btc-tuned", "coin": "BTC", "symbol": "BTCUSDT", "oos_sharpe": 1.58, "leverage": 3},
    {"name": "doge-tuned", "coin": "DOGE", "symbol": "DOGEUSDT", "oos_sharpe": 1.15, "leverage": 2},
]


def _meta(exp: dict) -> dict:
    """The parameter-category descriptor shown on the dashboard card."""
    return {
        "name": exp["name"], "coin": exp["coin"], "symbol": exp["symbol"],
        "variant": "調優 tuned",
        "category": f"{exp['coin']} · 調優 45天 · ≤{exp['leverage']}x",
        "lookback_days": TUNED["lookback_days"],
        "upper_pct": TUNED["upper"], "lower_pct": TUNED["lower"],
        "hold_days": TUNED["hold_days"],
        "leverage": exp["leverage"],
        "max_notional": START_EQUITY * exp["leverage"],
        "start_equity": START_EQUITY,
        "oos_sharpe": exp["oos_sharpe"],
    }


def tick_all() -> list[dict]:
    payloads = []
    params = _params()
    for exp in EXPERIMENTS:
        ledger_path = STATE / f"exp_{exp['name']}.json"
        store_path = STORE_DIR / f"live_{exp['symbol']}.parquet"
        max_notional = START_EQUITY * exp["leverage"]
        try:
            _led, payload = tick(
                params=params, symbol=exp["symbol"],
                start_equity=START_EQUITY, max_notional=max_notional,
                ledger_path=ledger_path, store_path=store_path,
                write_js=False, return_payload=True,
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
