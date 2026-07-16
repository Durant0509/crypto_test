# Research notes — Retail L/S Reversion

All runs use the shared backtest engine, baseline params unless noted
(90d lookback, 90/10 percentile, 3-day hold, no stop, inverse-vol 0.25-3x,
fees 0.04% + slip 0.02%/side), 2022-01-01 .. 2026-06-30. Reproduce with
`research/multi_coin.py`, `research/param_sweep.py`, `research/leverage_safety.py`,
then `scripts/build_research.py` to refresh the dashboard. **All in-sample.**

## 1. Coin universe (14 coins)

Baseline (BTC params) Sharpe: BTC **1.31** > ADA 1.10 > AVAX 0.92 > ETH 0.80 >
DOGE 0.76 > SOL 0.60 > LINK 0.48 > SUI 0.33 > BNB 0.05 > XRP −0.03 > TRX −0.05 >
LTC −0.07 > DOT −0.48 > 1000PEPE −0.64.

Tuned (each coin's own best lookback) Sharpe: **ADA 1.61 (45d)** ≈ **BTC 1.55 (45d)**
> DOGE 1.18 (30d) > AVAX 1.03 (60d) > ETH/SUI 0.84 > SOL 0.74 > rest < 0.6.

**Findings**
- **BTC + ADA are the only two solid, leverage-survivable names** (tuned Sharpe ~1.5+,
  MaxDD −18/−21%, worst per-trade adverse excursion −13/−24%). ADA tuned slightly
  *beats* BTC — consistent with the report's "retail-heavy coins" thesis; DOGE tuned
  is decent (1.18) but higher DD/MAE.
- **Most alts are ruin coins on leverage**: TRX −117%, DOT −78%, XRP −106%, LTC −34%,
  1000PEPE −58% worst adverse excursion → liquidated at low leverage. No-stop +
  idiosyncratic squeeze = blow-up. BNB (exchange token, less retail) is flat.
- Takeaway: run a **coin whitelist (BTC/ADA, maybe small DOGE)**, avoid the rest, or
  go cross-sectional market-neutral to diversify the single-coin blow-ups.

## 2. Parameter & timeframe sensitivity (BTC)

- **Best in-sample config = lookback 45d** (else unchanged): Sharpe **1.55** (vs 1.31),
  total **+312%** (vs +257%), MaxDD **−18.2%** (vs −24.5%), win 55.2%, worst MAE
  **−13.0%** (vs −16.4%). Improves return, drawdown, win rate AND leverage safety at
  once. **But it's in-sample** — 90d was the report's conservative walk-forward pick;
  45d must pass walk-forward before trusting.
- **Thresholds**: 90/10 is the sweet spot; 80/20 trades more (~4.5d/trade) for slightly
  less Sharpe; 95/5+ too sparse (8–12d/trade).
- **Hold**: 3 days is the peak. Longer holds explode MaxDD (5d −62%, 7d −66%) — with no
  stop, time in market = tail risk.
- **Vol targeting** helps: fixed-1x Sharpe 1.20 → vol-target 1.29–1.33. Higher target =
  more return + more DD; Sharpe fairly flat (robust).
- **Parameter plateau**: lookback[45,90] × hold[2,4] is a stable high-Sharpe block, not
  a lone spike — good sign it's not overfit noise.
- **Timeframe**: 1h/2h/4h all ~1.3 Sharpe, but **4h has much lower MaxDD (−15.8%) and
  higher win (56%)** — same edge, smoother ride.

## 3. Leverage safety (NO stop-loss → leverage is the only guardrail)

Per-trade MAE = worst intra-hold price move against the position (from high/low),
unlevered. Isolated-margin liquidation at leverage L when adverse ≈ 1/L − mmr.

- **BTC**: worst single-trade MAE **−16.4%**, median −2.0%. Zero liquidations up to
  **5×**; 7× loses 1 trade, 10× loses 3, 20× loses 52 (ruin). Theoretical max 5.9×.
  **Recommended ≤ 3×** (buffered — history isn't the future ceiling; no-stop house cap).
- By coin: BTC safe ~5× / rec 3×; ETH/ADA/DOGE/BNB ~3× / rec 2×; SOL 2× / rec 1×;
  **XRP liquidates even at 1×** (−106% MAE) — avoid at any leverage.
- The vol-sizing multiplier (0.25–3×) changes capital deployed, not the per-position
  liquidation price. MAE from hourly high/low can understate a real intra-bar spike.

## 4. Walk-forward validation (`research/walk_forward.py`, per-coin, independent)

14 rolling windows (365d train / 90d test / 90d step). On each train window the best
lookback (30/45/60/90d) is picked and applied to the unseen test window. Coins run
INDEPENDENTLY (no portfolio mixing).

| Coin | OOS fixed-45d | OOS fixed-90d | OOS adaptive | % windows + |
|---|---|---|---|---|
| BTC | **1.58** | 1.44 | 1.46 | 79% |
| ADA | **1.68** | 1.14 | 1.41 | 86% |
| DOGE | **1.15** | 0.52 | 0.93 | 79% |
| ETH | 0.50 | **0.73** | 0.49 | 64% |

**Findings**
- **45d lookback holds up out-of-sample** for BTC/ADA/DOGE — the in-sample win is NOT
  overfit (BTC OOS 1.58 even beats its in-sample 1.55). Train windows independently keep
  re-picking 45d (BTC 7/14, ADA 10/14) — a real parameter plateau.
- **ETH is the exception**: 45d (0.50) < 90d (0.73) OOS, weakest edge, only 64% windows
  positive. ADA is the most consistent (86%).
- **Fixed 45d beats per-window adaptive re-optimization** — constant re-tuning adds noise;
  a stable parameter is more robust. Don't over-optimize.
- Whitelist: BTC + ADA strong, DOGE ok, ETH marginal. Candidate: move live config to 45d.

## 5. Exit redesign — normalize exit (ADOPT for BTC/ADA, not DOGE) (2026-07)

The fixed 3-day time exit is the weakest link. Tested "exit on normalization": close
when the L/S percentile returns to a neutral band, with a max-hold cap
(`research/exit_variants.py`; engine gained backward-compatible `exit_mode`).

Best config **normalize, neutral .40-.60, 5-day cap** — walk-forward OOS Sharpe:

| Coin | baseline 3d (OOS) | normalize .40-.60/5d (OOS) |
|---|---|---|
| BTC | 1.58 | **1.73** |
| ADA | 1.68 | **1.82** |
| DOGE | 1.15 | **0.44** (much worse) |

**Findings**
- Normalize exit **genuinely helps BTC & ADA out-of-sample** (+0.1–0.15 Sharpe) — the
  reversion completes cleanly, so exiting when the percentile re-enters neutral frees
  capital sooner. Win rate drops but Sharpe rises (fewer stale holds).
- **DOGE is the opposite**: too retail/noisy, its percentile whipsaws through neutral
  before price reverts → normalize exits too early. **Keep fixed 3d for DOGE.**
- Verdict: **BTC/ADA → normalize exit (.40-.60, 5d cap); DOGE → fixed 3d.** Per-coin
  differentiation is OOS-validated + economically grounded, not curve-fit. Note: to run
  this live, `paper.py` needs the same normalize-exit logic (not yet wired).

## 6. Multi-factor confluence — TESTED, REJECTED (2026-07)

Idea: use the other factors in the metrics dumps (top-trader L/S accounts+positions,
taker vol ratio, OI) to confirm the retail signal. Followed STRATEGY_SOP.md.

- **IC/IR first** (`research/factors.py`, IC vs forward 72h return): retail L/S IC
  −0.08 (BTC, fade works). But **top-trader L/S is ALSO negative** (−0.089 acct /
  −0.072 pos) — top traders are *another contrarian crowd*, not smart money leading
  price. Taker-vol IC ≈ 0 (useless); OI Δ% −0.035 (weak). **This refutes the
  "divergence" hypothesis** — retail & top-trader move together.
- **Confluence backtest** (`research/confluence.py`, revised to "agreement"):
  gating on retail AND top-trader both extreme roughly HALVES Sharpe —
  BTC 1.55→0.73, ADA 1.61→0.75, DOGE 1.07→0.31 — cutting trades to ~1/3 (17–20d
  apart). Win rate ticks up (~+3-5pt) but nowhere near enough to offset. Blend
  (average percentile) also worse.
- **Verdict: keep the single retail factor.** No walk-forward needed — it failed
  the in-sample gate. Valuable negative result: don't re-explore this with these
  factors. (Untried, would need external data: funding-rate overlay, on-chain
  SOPR/NUPL.)

## 7. Ideas / extensions (ranked — see dashboard 未來方向 tab)

1. Cross-sectional market-neutral basket (diversify single-coin blow-ups).
2. Coin whitelist filter (BTC/ADA/small DOGE; skip BNB + ruin coins).
3. Shorten lookback to 45d (in-sample win; needs walk-forward).
4. Catastrophic-only hard stop (~−20%), not a tight stop.
5. Adopt 2h/4h timeframe (or 1h+4h ensemble) for lower drawdown.
6. Multi-factor blend: top-trader ratio, taker ratio, OI (already in metrics dumps) +
   funding + on-chain SOPR/NUPL — enter when retail crowded AND smart-money diverges.
7. Exit on normalization (percentile back to 40–60%) instead of fixed 3 days.
8. ADX regime switch — only fade in ranging markets (ADX<25).
9. ML meta-labeling filter (predict "will this reversion succeed?").
10. Robustness suite: walk-forward + Monte Carlo + IC/IR before trusting any change.
