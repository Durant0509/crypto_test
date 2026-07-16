"""
Event-driven backtest for the Retail Long/Short-Ratio Reversion strategy.

Design choices that keep the result honest:
  * No look-ahead: a signal computed from bar t's close/ratio is executed at the
    OPEN of bar t+1 (you can only act after you've seen the hourly close).
  * One position at a time, one action per candle (matches the live bot's
    "same K-line never double-orders" idempotency guarantee).
  * Fixed 3-day time exit, NO stop-loss (the spec's core, counter-intuitive
    design). A position is marked-to-market every hour so the equity curve and
    drawdown reflect the unrealised pain you'd actually sit through.
  * Fixed nominal sizing, no compounding: every trade risks `base_notional`
    scaled by the inverse-vol multiplier. Returns are P&L / base_notional, so
    they're comparable across the whole period regardless of account growth.
  * Costs (taker fee + slippage) charged on entry and on exit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..strategy.signal import FLAT, Params, compute


@dataclass
class Costs:
    fee_rate: float = 0.0004       # Binance USD-M taker fee per side (0.04%)
    slippage: float = 0.0002       # assumed slippage per side (0.02%)

    @property
    def per_side(self) -> float:
        return self.fee_rate + self.slippage


@dataclass
class BacktestConfig:
    base_notional: float = 1000.0
    start: str = "2022-01-01"      # trading starts here (data before = warmup)
    end: str | None = None
    params: Params = field(default_factory=Params)
    costs: Costs = field(default_factory=Costs)
    # exit logic. "time" = fixed hold_hours (original). "normalize" = exit when the
    # L/S percentile returns to the neutral band (reversion done), with hold_hours
    # as a MAX cap and min_hold_hours before normalize-exit is allowed.
    exit_mode: str = "time"
    neutral_lo: float = 0.40
    neutral_hi: float = 0.60
    min_hold_hours: int = 24


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series              # hourly, mark-to-market, starts at base_notional
    signals: pd.DataFrame          # full signal frame (for inspection)
    config: BacktestConfig


def run(df: pd.DataFrame, cfg: BacktestConfig = BacktestConfig(),
        signals: pd.DataFrame | None = None) -> BacktestResult:
    # `signals` lets research pass a pre-computed frame (same open/high/low/close +
    # target/size columns) to test alternative signals without touching the shared
    # signal code. Default None reproduces the standard single-factor behaviour.
    sig = signals if signals is not None else compute(df, cfg.params)

    start = pd.Timestamp(cfg.start, tz="UTC")
    end = pd.Timestamp(cfg.end, tz="UTC") if cfg.end else sig.index.max()
    mask = (sig.index >= start) & (sig.index <= end)
    win = sig.loc[mask]

    opens = win["open"].to_numpy(dtype=float)
    highs = win["high"].to_numpy(dtype=float)
    lows = win["low"].to_numpy(dtype=float)
    closes = win["close"].to_numpy(dtype=float)
    targets = win["target"].to_numpy(dtype=int)
    sizes = win["size"].to_numpy(dtype=float)
    pcts = win["pct"].to_numpy(dtype=float)        # L/S percentile (for normalize exit)
    idx = win.index
    n = len(win)
    normalize = cfg.exit_mode == "normalize"
    nlo, nhi, min_hold = cfg.neutral_lo, cfg.neutral_hi, cfg.min_hold_hours

    base = cfg.base_notional
    cps = cfg.costs.per_side
    hold = cfg.params.hold_hours

    realized = 0.0
    equity = np.full(n, base, dtype=float)
    trades: list[dict] = []

    pos_side = 0          # 0 flat, +1 long, -1 short
    entry_i = -1
    entry_px = 0.0
    notional = 0.0
    # worst / best UNLEVERED price excursion against/with the position over the
    # hold (mae<=0, mfe>=0). Used by the leverage-safety analysis: a position at
    # leverage L is liquidated when the adverse price move reaches ~1/L.
    mae = 0.0
    mfe = 0.0

    def _record_trade(exit_i, exit_px, cost):
        gross = pos_side * (exit_px / entry_px - 1.0)
        pnl = notional * gross - cost
        trades.append({
            "entry_time": idx[entry_i], "exit_time": idx[exit_i],
            "side": "LONG" if pos_side == 1 else "SHORT",
            "entry_px": entry_px, "exit_px": exit_px,
            "size_mult": notional / base, "notional": notional,
            "gross_return": gross, "pnl": pnl, "win": pnl > 0,
            "hold_hours": exit_i - entry_i,
            "mae": mae, "mfe": mfe,        # worst / best price excursion in hold
        })
        return pnl

    for i in range(1, n):
        # ----- exit: executed at this bar's open, on the prior bar's signal ---
        if pos_side != 0:
            held = i - entry_i
            time_exit = held >= hold                       # hold_hours = time exit / max cap
            norm_exit = (normalize and held >= min_hold
                         and np.isfinite(pcts[i - 1]) and nlo <= pcts[i - 1] <= nhi)
            if time_exit or norm_exit:
                realized += _record_trade(i, opens[i], notional * cps * 2.0)  # 2 legs
                pos_side, entry_i, entry_px, notional, mae, mfe = 0, -1, 0.0, 0.0, 0.0, 0.0

        # ----- entry: act on the previous bar's signal, at this bar's open ---
        if pos_side == 0 and targets[i - 1] != FLAT:
            mult = sizes[i - 1]
            if np.isfinite(mult) and mult > 0:
                pos_side = int(targets[i - 1])
                entry_i = i
                entry_px = opens[i]
                notional = base * mult
                mae = mfe = 0.0
                # entry cost realised immediately (felt in equity right away)
                realized -= notional * cps

        # ----- track intra-hold adverse/favorable excursion (high/low) -------
        if pos_side != 0:
            hi_ex = pos_side * (highs[i] / entry_px - 1.0)
            lo_ex = pos_side * (lows[i] / entry_px - 1.0)
            worst, best = min(hi_ex, lo_ex), max(hi_ex, lo_ex)
            if worst < mae:
                mae = worst
            if best > mfe:
                mfe = best

        # ----- mark-to-market equity ----------------------------------------
        if pos_side != 0:
            unreal = notional * pos_side * (closes[i] / entry_px - 1.0)
            equity[i] = base + realized + unreal
        else:
            equity[i] = base + realized

    # close any position still open at the end, at the last close
    if pos_side != 0:
        realized += _record_trade(n - 1, closes[-1], notional * cps)  # exit leg only
        equity[-1] = base + realized

    trades_df = pd.DataFrame(trades)
    equity_s = pd.Series(equity, index=idx, name="equity")
    return BacktestResult(trades=trades_df, equity=equity_s, signals=win, config=cfg)
