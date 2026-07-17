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

## 6b. Funding rate — TESTED, REJECTED (2026-07)

Funding = perp 8h payment, a $-cost crowding measure (vs retail head-count). Data:
data.binance.vision monthly fundingRate dumps. `research/funding.py`.
- IC vs fwd 72h: BTC −0.055 (useful, negative=fade as expected), ADA −0.025 (weak),
  DOGE −0.005 (~0). Meaningful only on BTC.
- Standalone funding-extreme reversion: **fails badly** (OOS BTC −0.01, ADA −0.65,
  DOGE −0.97; MaxDD −80 to −126%). Funding extremes don't revert like retail L/S.
- Retail+funding "agree" confluence: **no OOS Sharpe gain** (BTC 1.58→1.08, ADA tie
  1.68, DOGE 0.33). One note: ADA-agree cuts MaxDD −21%→−14.5% + win 65% at tie-Sharpe
  (fewer trades, 122) — a possible risk-reduction angle, not a Sharpe win.

**META-LESSON (3 factors now):** top-trader L/S, Coinbase Premium, and funding all
have real IC / orthogonality but NONE improve out-of-sample Sharpe as a confirming
filter — they filter out too many good trades. The improvements that DID work are
strategy MECHANICS (45d lookback, normalize exit), not added factors. Stop hunting
for confirming factors; if anything, next explore genuinely different alpha
(liquidation cascades — needs self-collected data) or accept the single-factor edge.

## 6b. ADX regime filter TESTED & REJECTED (2026-07, `research/adx_regime.py`)

Hypothesis: only fade in ranging markets (daily ADX<threshold), stand aside in
strong trends. Causal daily-ADX(14), prior-day, ffill to hourly; gate target=FLAT
where ADX>=threshold; engine `signals=` override; fixed 45d both arms.
Walk-forward OOS (same rolling windows as baseline):

| coin | baseline | ADX<18 | ADX<22 | ADX<25 | ADX<30 |
|---|---|---|---|---|---|
| BTC  | **1.58** | 1.32 | 1.35 | 1.37 | 1.28 |
| ADA  | **1.68** | 0.09 | 0.49 | 1.05 | 1.05 |
| DOGE | 1.15     | 1.20 | **1.73** | 1.29 | 1.23 |
| ETH  | 0.50     | 0.61 | 0.55 | 0.54 | **0.86** |

Verdict: **REJECTED.** No threshold beats baseline for BTC/ADA (the strong
coins) — gating out high-ADX bars removes *profitable* fade trades, so Sharpe
drops (not just fewer trades). DOGE's 1.73 at ADX<22 is a **lone spike** (neighbors
1.20/1.29), not a plateau = overfit red flag. ETH only "helps" at the loosest gate
(30, barely filtering). The reversion edge is NOT concentrated in ranging regimes —
strong-trend crowd extremes still mean-revert. This is another instance of the
META-LESSON: adding a filter to the single factor removes too many good trades.

## 6c. Catastrophic-only stop TESTED & PARTIALLY ADOPTED (2026-07, `research/catastrophic_stop.py`)

Wide unlevered stop (engine `catastrophic_stop`), fixed 45d. Walk-forward OOS:

| coin | no-stop | -20% | -25% | note |
|---|---|---|---|---|
| BTC  | 1.58 | 1.58 | 1.58 | never triggers (worst trade -11.3% over 4.5y) — free |
| ADA  | 1.68 | 1.62 | 1.68 | -20% cuts 4 would-revert trades; -25%+ harmless |
| DOGE | 1.15 | 1.19 | 1.14 | full-period MaxDD WORSENS -42.6%→-50~59% |
| XRP (ruin) | Sharpe 0.18 / MaxDD **-91.7%** | 0.57 / **-41.3%** | 0.48 / -44% | stop caps the ruin tail |

Verdict: **PARTIAL / risk-mgmt, NOT a Sharpe raiser.** On the whitelist (which
already excludes ruin coins) the stop is at best neutral (BTC free, ADA needs
-25%) and HURTS DOGE — a mean-reversion strat that stops out locks reversible
losses into realized ones and *deepens* drawdown (this is the mathematical reason
the strategy is stop-less by design; data confirms it). Real value = ruin-cap
insurance: XRP -91.7%→-41.3% MaxDD. **Adopt -25% catastrophic stop on BTC/ADA as
a regime kill-cap (Sharpe unchanged); do NOT apply to DOGE.**

## 6d. Z-score entry vs percentile TESTED & REJECTED (2026-07, `research/zscore_entry.py`)

Replace percentile-rank entry with z-score (LSR-mean)/std over 45d. Walk-forward OOS:

| coin | percentile | z>=1.5 | z>=2.0 | z>=2.5 |
|---|---|---|---|---|
| BTC  | **1.58** | 1.50 | 0.66 | -0.03 |
| ADA  | 1.68 | 1.73 | 1.13 | 1.26 |
| DOGE | 1.15 | 1.32 | 0.52 | 0.65 |
| ETH  | 0.50 | 0.60 | -0.14 | -0.76 |

Verdict: **REJECTED (wash).** At comparable trade counts (z>=1.5, already stricter
than 90/10 percentile ≈ ±1.28σ) z-score is within noise of percentile — BTC
slightly worse, ADA/DOGE slightly better. Converging the threshold to ±1.28σ would
just reproduce percentile. Keep percentile (no distributional assumption, more
robust to fat tails). z-graded sizing (bigger sigma -> bigger bet) added nothing
(ADA 1.73→1.79, BTC 1.50→1.38). **KEY INSIGHT: Sharpe collapses monotonically as
the threshold tightens (BTC 1.50→0.66→-0.03) — the reversion edge lives in MILD
crowd extremes, not the deep tail; deep-sigma events are regime moves where the
crowd is RIGHT.** Same truth as the ADX rejection, and why 90/10 (not 95/5) is the
sweet spot. Grading size toward the deep tail bets exactly where the edge is worst.

## 6e. Signal-discovery scan — DIFFERENT underlying factors standalone (2026-07, `research/factor_scan.py`)

Rule change (Durant): a factor on a DIFFERENT underlying is judged on its OWN
merit (standalone OOS Sharpe > 1.2 = record + simulate), NOT vs the L/S Sharpe.
Common harness (45d extreme entry, 3d hold, inverse-vol), only the FACTOR varies;
direction pinned by economic thesis. Control (L/S fade) reproduces 1.58/1.68/1.15.

OOS Sharpe | corr-to-L/S:

| factor (underlying) | dir | BTC | ADA | DOGE | corrLS | verdict |
|---|---|---|---|---|---|---|
| L/S ratio (control) | fade | 1.58 | 1.68 | 1.15 | 1.00 | incumbent |
| taker vol ratio | fade | -0.86 | -0.32 | 0.25 | low | no edge |
| top-trader count L/S | fade | 0.49 | 1.13 | 0.45 | 0.69 | correlated weak cousin |
| top-trader sum L/S | fade | -0.44 | -0.30 | -0.74 | — | no edge |
| funding | fade | -0.01 | -0.65 | -0.97 | — | no edge (consistent w/ prior) |
| OI z-score | fade | -0.53 | -0.19 | -0.77 | — | naive OI has no direction |
| OI change | fade | -1.25 | -0.07 | -0.82 | — | no edge |
| **price reversion** | fade | **-1.67** | -1.43 | -1.56 | **-0.72** | fade LOSES big |
| **price momentum (MACD)** | follow | 0.50 | 0.17 | **1.49** | 0.26 | **DOGE clears 1.2 bar, orthogonal** |

TWO findings:
1. **New qualifying factor: DOGE price MOMENTUM, OOS Sharpe 1.49, corr 0.26 to the
   L/S sleeve** — a different underlying (price trend), nearly orthogonal. Record +
   simulate per the bar. (BTC 0.50 / ADA 0.17 weak — momentum not robust in this
   crude MACD form; needs a proper momentum-factor experiment.)
2. **At the 3-day horizon crypto is MOMENTUM, not mean-reversion**: fading price
   extremes loses hugely on all 3 coins (-1.4~-1.7) and is negatively correlated to
   the L/S sleeve (-0.72). We now have two low/negatively-correlated alpha
   directions: sentiment CONTRARIAN (L/S fade) + price TREND-FOLLOWING (momentum).
   A 2-sleeve combo is more promising than further L/S tuning.
3. Positioning/flow factors (taker, top-trader, funding, naive OI) have NO
   standalone edge — consistent with the factor meta-lesson. OI still deserves the
   quadrant (ΔOI x price-direction) test; naive-percentile OI is dead.

## 6f. BTC momentum factor TESTED & REJECTED (2026-07, `research/btc_momentum.py`)

BTC-first. 4 momentum constructions x lookback {7,14,21,30,45d}, 3d hold,
walk-forward OOS (ref: BTC L/S sleeve 1.58):

| construction | best OOS | across N | corr to L/S |
|---|---|---|---|
| tsmom_sign (always-in) | 0.79 | 0.05/0.68/-0.06/0.79/0.25 (noisy) | 0.44-0.50 |
| tsmom_extreme (follow) | 1.26 @N14 | neighbors N7=0.40, N21=0.40 = LONE SPIKE | 0.63 |
| price_zscore_follow | 1.03 @N45 | 0.5-1.03 | 0.63-0.72 |
| donchian breakout | 0.96 @N21 | 0.54-0.96 | 0.57-0.68 |

Verdict: **REJECTED.** No construction robustly clears 1.2 — the only >1.2
(tsmom_extreme N=14=1.26) is a lone spike (neighbors 0.40) = overfit red flag.
Corrects the earlier optimistic read: the price-fade -1.67 (scan §6e) is largely a
MIRROR of the L/S sleeve's own exposure, not an independent BTC momentum alpha —
price-follow momentum is POSITIVELY correlated (0.6-0.72) with the L/S sleeve, so
it doesn't even diversify. The only orthogonal momentum (MACD, corr 0.26) is weak
(0.50). **BTC's edge remains the L/S contrarian sleeve (1.58).** (DOGE momentum
1.49 @corr 0.26 was the more genuinely independent hit, but DOGE is deprioritized.)

## 6g. BTC momentum REVISITED — QUALIFIES (borderline) after hold tuning (2026-07, `research/btc_momentum_refine.py` + `btc_momentum_confirm.py`)

Supersedes §6f's rejection. §6f strangled momentum with the L/S strategy's 3d hold.
Momentum wants to RIDE: sweeping the hold on tsmom_extreme N=30 (follow 45d-pct
extreme of 30d return):

| hold | OOS Sharpe | win-windows |
|---|---|---|
| 6d | 1.24 | 12/14 |
| 7d | 1.30 | 12/14 |
| 8d | 1.08 | 12/14 |
| 9d | 1.00 | 11/14 |
| 10d | 1.28 | 12/14 |
| 11d | 0.93 | 9/14 |

Verdict: **QUALIFIES (borderline) per the >1.2 own-merit bar** — NOT vs L/S.
Honest read: real but modest edge. STRONG signal it's not overfit — per-window
totals at 7d = [18.7,28.6,21.9,13.6,39.9,13.3,9.8,14.7,9.3,20.1,-7.7,-12.9,29.8,30.1],
12/14 positive, broadly spread (not 1-2 lucky windows). BUT the hold surface is
BUMPY (6-7d high, 8-9d dips to ~1.0, 10d spikes) => true Sharpe sits ~1.0-1.3 right
on the bar; don't oversell 1.30. corr to L/S ~0.53 (moderate — partial diversifier,
not orthogonal). Config: tsmom_extreme N=30, hold 7d. Let forward paper arbitrate
whether it's really ~1.3 or ~1.0 before real capital.

## 6h. METHODOLOGY FIX — walk-forward warmup blind spot (2026-07, `research/wf.py`)

**Bug**: the 365d-train/90d-test roll from 2022-01-01 made the first TEST window
start 2023-01-01, so ALL of 2022 (LUNA/FTX bear) was always warmup, NEVER tested.
The concatenated OOS curve silently started in 2023 and skipped the worst regime →
systematically INFLATED regime-dependent strategies. Found via a 1.22 (OOS) vs 0.74
(full-period) discrepancy on momentum.

**Fix**: `research/wf.py::evaluate()` reports BOTH `oos_2023` (walk-forward) AND
`full_2022` (single fixed-config backtest incl. 2022). GAP = oos − full = a
regime-dependence diagnostic. All future experiments use it.

Re-validation (OOS-2023 | FULL-2022 | gap):

| strategy | OOS23 | FULL22 | gap | 2022 |
|---|---|---|---|---|
| BTC L/S 3d | 1.58 | **1.55** | 0.03 | +42.7% |
| BTC L/S normalize | 1.73 | 1.57 | 0.16 | — |
| ADA L/S 3d | 1.68 | **1.61** | 0.07 | +50.7% |
| ADA L/S normalize | 1.82 | 1.58 | 0.24 | — |
| DOGE L/S 3d | 1.15 | **1.07** | 0.08 | +58.5% |
| BTC momentum Donch-5 | 1.22 | **0.74** | 0.48 ⚠ | -59.1% |

Findings:
- **L/S mainline is ROBUST, not inflated** (gap ≤0.08 on 3d): it earns big in the
  2022 bear (market-neutral), so skipping 2022 barely mattered. The adopted
  1.55-1.61 full-period Sharpes stand.
- **normalize-exit advantage SHRINKS** with 2022 included (gap 0.16-0.24): BTC
  full 1.57 ≈ 3d's 1.55 (tie); ADA normalize full 1.58 < 3d's 1.61. The
  previously-recorded 1.73/1.82 leaned on skipping 2022. Downgrade normalize from
  "clear win" to "roughly tie / marginal" — let live A/B arbitrate.
- **Momentum CONFIRMED FAILING**: full-period 0.74, MaxDD -59.7%, 2022 -59.1% —
  below the 1.2 bar. Low win-rate (33.8%) trend strategy that lives/dies on trend
  years. Donchian-k exit itself was sound (smoother than fixed-hold) but can't save
  a regime-fragile entry.

## 6i. BTC momentum FINAL VERDICT — REJECTED on full-period (2026-07)

tsmom_extreme N=30 + Donchian-5 trailing exit. Donchian-k plateau (k=5-8 all
1.16-1.25 OOS, MaxDD ~-16%) confirmed condition-exit > fixed-hold (Durant's
insight — the bumpy fixed-hold surface was a clock-exit artifact). Timeframe scan:
1h best (1.22 OOS), 2h/4h similar, 1d bad (0.32). BUT full-period incl. 2022 =
0.74 / MaxDD -59.7%. **Below the 1.2 own-merit bar. Rejected as a standalone BTC
sleeve.** Keep the Donchian-trailing-exit + wf.py machinery for future factors.

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
