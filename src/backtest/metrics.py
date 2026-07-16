"""Performance metrics for a BacktestResult."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import BacktestResult

HOURS_PER_YEAR = 24 * 365


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough decline of the (mark-to-market) equity curve."""
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def sharpe(daily_ret: pd.Series) -> float:
    sd = daily_ret.std()
    if sd == 0 or np.isnan(sd):
        return float("nan")
    return float(daily_ret.mean() / sd * np.sqrt(365))


def summary(res: BacktestResult, price: pd.Series) -> dict:
    base = res.config.base_notional
    eq = res.equity
    tr = res.trades

    # returns on fixed notional (no compounding): delta-equity / base
    daily_eq = eq.resample("1D").last().dropna()
    daily_ret = daily_eq.diff().fillna(daily_eq.iloc[0] - base) / base

    total_ret = float(eq.iloc[-1] / base - 1.0)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    # fixed-notional / no-compounding equity can go below zero on a blow-up
    # (no stop), which makes (1+total_ret)^(1/y) complex — guard it.
    cagr = ((1.0 + total_ret) ** (1 / years) - 1.0
            if years > 0 and total_ret > -1.0 else float("nan"))

    # buy & hold over the same trading window
    px = price.loc[(price.index >= eq.index[0]) & (price.index <= eq.index[-1])]
    bh_total = float(px.iloc[-1] / px.iloc[0] - 1.0)
    bh_daily = np.log(px.resample("1D").last().dropna()).diff().dropna()

    # correlation of daily returns with BTC
    strat_d = daily_ret.reindex(bh_daily.index).dropna()
    common = strat_d.index.intersection(bh_daily.index)
    corr = float(np.corrcoef(strat_d.loc[common], bh_daily.loc[common])[0, 1]) if len(common) > 2 else float("nan")

    wins = tr[tr["win"]] if len(tr) else tr
    losses = tr[~tr["win"]] if len(tr) else tr

    # yearly strategy return (fixed notional) + buy&hold
    yearly = {}
    for yr, grp in eq.groupby(eq.index.year):
        start_eq = grp.iloc[0]
        # use prior year's last equity as the base point if available
        prev = eq[eq.index.year < yr]
        start_ref = prev.iloc[-1] if len(prev) else base
        yearly[int(yr)] = {
            "strategy": float((grp.iloc[-1] - start_ref) / base),
        }
    for yr, grp in px.groupby(px.index.year):
        prev = px[px.index.year < yr]
        start_ref = prev.iloc[-1] if len(prev) else grp.iloc[0]
        yearly.setdefault(int(yr), {})["buy_hold"] = float(grp.iloc[-1] / start_ref - 1.0)

    return {
        "period": f"{eq.index[0].date()} .. {eq.index[-1].date()}",
        "years": round(years, 2),
        "total_return": total_ret,
        "cagr": cagr,
        "buy_hold_return": bh_total,
        "sharpe": sharpe(daily_ret),
        "max_drawdown": max_drawdown(eq),
        "num_trades": int(len(tr)),
        "num_long": int((tr["side"] == "LONG").sum()) if len(tr) else 0,
        "num_short": int((tr["side"] == "SHORT").sum()) if len(tr) else 0,
        "win_rate": float(tr["win"].mean()) if len(tr) else float("nan"),
        "avg_win": float(wins["pnl"].mean()) if len(wins) else 0.0,
        "avg_loss": float(losses["pnl"].mean()) if len(losses) else 0.0,
        "profit_factor": (float(wins["pnl"].sum() / -losses["pnl"].sum())
                          if len(losses) and losses["pnl"].sum() != 0 else float("inf")),
        "total_pnl": float(tr["pnl"].sum()) if len(tr) else 0.0,
        "btc_daily_corr": corr,
        "yearly": yearly,
    }


def format_summary(s: dict) -> str:
    L = []
    L.append(f"Period            : {s['period']}  ({s['years']} yrs)")
    L.append(f"Cumulative return : {s['total_return']*100:+.1f}%   (buy&hold BTC: {s['buy_hold_return']*100:+.1f}%)")
    L.append(f"CAGR              : {s['cagr']*100:+.1f}%")
    L.append(f"Sharpe (daily)    : {s['sharpe']:.2f}")
    L.append(f"Max drawdown      : {s['max_drawdown']*100:.1f}%")
    L.append(f"Trades            : {s['num_trades']}  (long {s['num_long']} / short {s['num_short']})")
    L.append(f"Win rate          : {s['win_rate']*100:.1f}%")
    L.append(f"Avg win / loss    : +{s['avg_win']:.1f} / {s['avg_loss']:.1f}  (base notional {1000})")
    L.append(f"Profit factor     : {s['profit_factor']:.2f}")
    L.append(f"Total P&L         : {s['total_pnl']:+.1f}  (on 1000 nominal)")
    L.append(f"Daily corr w/ BTC : {s['btc_daily_corr']:+.2f}")
    L.append("Yearly returns    :")
    for yr in sorted(s["yearly"]):
        d = s["yearly"][yr]
        strat = d.get("strategy")
        bh = d.get("buy_hold")
        strat_s = f"{strat*100:+.0f}%" if strat is not None else "  n/a"
        bh_s = f"{bh*100:+.0f}%" if bh is not None else "n/a"
        L.append(f"    {yr}: strategy {strat_s:>6}   buy&hold {bh_s:>6}")
    return "\n".join(L)
