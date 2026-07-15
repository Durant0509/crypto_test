"""
Run one paper-trading tick: read the live signal, update the virtual 1000 USDT
account, persist the ledger, and regenerate docs/live.js for the dashboard.

Meant to be run once per hour (GitHub Actions or cron). Idempotent per candle.

    python scripts/paper_tick.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.live.paper import tick   # noqa: E402


def main():
    led = tick()
    p = led["position"]
    pos = f"{p['side']} since {p['entry_candle'][:16]}" if p else "flat"
    print(f"paper tick ok | equity={led['equity']:.2f} | position={pos} | trades={len(led['trades'])}")


if __name__ == "__main__":
    main()
