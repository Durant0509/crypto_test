"""
Signal & position sizing for the Retail Long/Short-Ratio Reversion strategy.

This module is the single source of truth for "what would the strategy do right
now", and is shared by BOTH the backtest engine and the live testnet bot so the
two can never drift apart.

Rules (from the strategy spec):
  * Look at where the current global long/short ACCOUNT ratio sits within its own
    trailing 90-day distribution (percentile rank).
  * ratio in the top 10%  (>= 90th pct)  -> retail crowded LONG  -> we go SHORT.
  * ratio in the bottom 10% (<= 10th pct) -> retail crowded SHORT -> we go LONG.
  * Otherwise: no entry.
  * Position size scales inversely with recent volatility (calm -> bigger,
    turbulent -> smaller), clipped to a sane band.

Deliberately NO stop-loss and a fixed 3-day time exit — both live in the engine,
not here; this module only answers "direction + size" for a given bar.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LONG, SHORT, FLAT = 1, -1, 0


@dataclass(frozen=True)
class Params:
    lookback_hours: int = 90 * 24      # 90-day percentile window
    upper_pct: float = 0.90            # >= this -> short
    lower_pct: float = 0.10            # <= this -> long
    hold_hours: int = 3 * 24           # 3-day time exit
    vol_window_hours: int = 72         # realized-vol window for sizing
    target_daily_vol: float = 0.025    # vol-target for position sizing
    size_min: float = 0.25             # min leverage multiplier
    size_max: float = 3.0              # max leverage multiplier


def percentile_rank(lsr: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank of the latest value within its trailing window.

    Returns a value in [0, 1]: the fraction of the last `window` observations
    (inclusive of the current one) that are <= the current value. NaN until the
    window is full.
    """
    def _rank(a: np.ndarray) -> float:
        return float((a <= a[-1]).mean())

    return lsr.rolling(window, min_periods=window).apply(_rank, raw=True)


def realized_daily_vol(close: pd.Series, window: int) -> pd.Series:
    """Trailing realized volatility, expressed on a daily scale."""
    ret = np.log(close).diff()
    return ret.rolling(window, min_periods=window // 2).std() * np.sqrt(24.0)


def position_size(daily_vol: pd.Series, p: Params) -> pd.Series:
    """Inverse-vol sizing multiplier, clipped to [size_min, size_max]."""
    size = p.target_daily_vol / daily_vol.replace(0.0, np.nan)
    return size.clip(lower=p.size_min, upper=p.size_max)


def compute(df: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """Attach signal columns to an hourly frame with a `close` and `lsr` column.

    Adds:
      pct    - rolling percentile rank of the long/short ratio (0..1)
      target - desired direction for a *new* entry this bar (LONG/SHORT/FLAT)
      size   - inverse-vol position multiplier
    """
    out = df.copy()
    out["pct"] = percentile_rank(out["lsr"], p.lookback_hours)
    out["dvol"] = realized_daily_vol(out["close"], p.vol_window_hours)
    out["size"] = position_size(out["dvol"], p)

    target = pd.Series(FLAT, index=out.index, dtype=int)
    target[out["pct"] >= p.upper_pct] = SHORT
    target[out["pct"] <= p.lower_pct] = LONG
    # No signal where inputs are missing.
    target[out["pct"].isna() | out["lsr"].isna()] = FLAT
    out["target"] = target
    return out
