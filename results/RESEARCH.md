# Research notes — Retail L/S Reversion

All runs use the shared backtest engine, identical baseline params
(90d lookback, 90/10 percentile, 3-day hold, no stop, inverse-vol 0.25-3x,
fees 0.04% + slip 0.02%/side), 2022-01-01 .. 2026-06-30. Reproduce with
`research/multi_coin.py` and `research/param_sweep.py`. **All in-sample.**

## 1. Other coins (same params, sorted by Sharpe)

| Symbol | Sharpe | Total | Buy&Hold | MaxDD | Trades | Win% | Corr BTC |
|---|---|---|---|---|---|---|---|
| BTCUSDT | **1.31** | +257% | +28% | -24.5% | 259 | 52.5% | -0.08 |
| ADAUSDT | 1.10 | +214% | -89% | -27.6% | 266 | 53.0% | +0.23 |
| ETHUSDT | 0.80 | +154% | -57% | -30.9% | 277 | 52.0% | -0.02 |
| DOGEUSDT | 0.76 | +157% | -58% | -41.6% | 275 | 47.3% | +0.05 |
| SOLUSDT | 0.60 | +99% | -57% | -35.3% | 248 | 50.4% | -0.04 |
| BNBUSDT | 0.05 | +8% | +7% | -52.6% | 254 | 52.0% | -0.05 |
| XRPUSDT | -0.03 | -9% | +25% | **-116%** | 260 | 48.8% | -0.31 |

**Findings**
- **BTC has the best Sharpe**, not the meme coins — this *contradicts* the source
  report's "strongest on DOGE/ADA" claim (at least with BTC-tuned params, in-sample).
- The strategy still **crushes buy&hold on alts** (ADA B&H -89% vs strategy +214%)
  — its value is being roughly market-neutral, not beating B&H per se.
- **XRP is a blow-up**: -116% drawdown = on a leveraged account it would have been
  liquidated. No stop + leverage + an idiosyncratic squeeze (SEC-case pumps) = ruin.
- BNB (exchange token, less retail-driven) is flat — consistent with "edge needs a
  crowded retail counterparty."
- Takeaway: run a **coin universe filter** (BTC/ADA/ETH), avoid XRP-like names, or
  go cross-sectional (see ideas).

## 2. Parameter & timeframe sensitivity (BTC)

- **Lookback**: peaks ~45-60d (Sharpe 1.55 / 1.45) vs 90d baseline 1.31; degrades >120d.
  90d is a *conservative* choice (likely from walk-forward); 45-60d looks better in-sample.
- **Thresholds**: 90/10 (1.31) is the sweet spot; 80/20 trades more for slightly less
  Sharpe; 95/5+ too sparse.
- **Hold**: 3 days is the peak (1.31). Longer holds don't just lower Sharpe — MaxDD
  explodes (5d -62%, 7d -66%): with no stop, time in market = tail risk.
- **Vol targeting helps**: fixed-1x Sharpe 1.20 → vol-target 1.29-1.33. Higher target =
  more return + more DD; Sharpe fairly flat 0.015-0.04 (robust).
- **Parameter plateau**: the lookback∈[45,90] × hold∈[2,4] block is a stable high-Sharpe
  region (not a lone spike) — good sign it's not overfit noise.
- **Timeframe**: 1h/2h/4h all ~1.3 Sharpe, but **4h has much lower MaxDD (-15.8%) and
  higher win rate (56%)** — same edge, smoother ride. 2h/4h look like a better risk profile.

## 3. Ideas / extensions

1. **Cross-sectional market-neutral basket** (highest priority). Each hour, rank the
   L/S-ratio percentile across ~10 liquid perps; short the most crowded-long, long the
   most crowded-short, dollar-neutral. Diversifies away single-coin blow-ups (XRP),
   removes directional beta, and is the "complementary strategy" the report hinted at.
2. **Catastrophic-only circuit breaker**, not a tight stop. A wide stop (~ -20% adverse,
   or a same-bar -15% crash guard) to prevent XRP-style ruin without the +74%→-20% death
   a 3-5% stop causes. Worth a sweep to find the stop level that cuts tail risk with
   minimal Sharpe cost.
3. **Universe / coin filter**: only trade names with a persistent retail edge
   (BTC/ADA/ETH), skip institutional (BNB) and idiosyncratic (XRP) coins.
4. **Adopt 4h timeframe** (or 1h+4h ensemble): from the sweep, similar Sharpe, ~35% less
   drawdown, higher win rate.
5. **Signal confirmation / blend**: combine the account L/S ratio with the other columns
   already in the metrics dumps — top-trader position ratio, taker buy/sell volume — plus
   funding rate & OI. Enter only when retail is crowded *and* smart-money diverges.
6. **Exit on normalization** instead of fixed 3 days: close when the percentile returns to
   neutral (say 40-60%). Frees capital when the reversion completes early; test vs fixed time.
7. **Funding-rate overlay**: over a 3-day perp hold, funding P&L is material — tilt toward
   positions where funding pays you, size down where it bleeds.
