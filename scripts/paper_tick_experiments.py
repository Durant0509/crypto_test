"""
Run one paper-trading tick for EACH of the multi-coin experiments (ADA/BTC/DOGE,
tuned 45d config, per-coin recommended leverage). Independent of the original
single-BTC bot (scripts/paper_tick.py) — run both hourly.

Regenerates docs/live_experiments.js for the dashboard. Idempotent per candle.

    python scripts/paper_tick_experiments.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.live.experiments import tick_all   # noqa: E402


def main():
    payloads = tick_all()
    ok = sum(1 for p in payloads if "error" not in p)
    print(f"experiments tick ok | {ok}/{len(payloads)} experiments live")


if __name__ == "__main__":
    main()
