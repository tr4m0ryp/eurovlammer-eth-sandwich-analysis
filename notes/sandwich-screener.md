# Sandwich-target screener: which swaps do sandwichers pick?

## Vision
Bachelor thesis (CS), market microstructure and liquidity on Solana AMMs. Build a live screener that
decodes every pump.fun / PumpSwap swap, computes exposure metrics, and shows only the swaps a
sandwicher would select. "Interesting" is not given: derive it from data, robust to per-pool fees and
to fast scalpers paying high priority fees. Page in the trenches / Avelero style.

Non-goals: no trading, signing or submitting (read-only on chain).

## Vision Updates
- 2026-10-14: AWS account trenches (AWS_ACCOUNT_ID) is in scope for this project; use it for bulk pulls.
- 2026-10-14: overnight collection of **risk** swaps = theoretically sandwichable (slack-capped MESP above
  the attacker's two-transaction gas), each with sandwich gain (SOL, % of trade, return on front-run),
  a top-10%-gain label and the real sandwiched tag. Run unattended (cron watchdog), export in the morning.
- 2026-10-14: risk = **buy-side only** (attacker buys before, sells after). User's call, confirmed by data:
  91% of 7,563 real attacks in the 5 h block front-run a buy. Attacker front capped at 5 SOL (p99 of
  real fronts 3.0 SOL, max 6.9). User hypothesis: buy size relative to market cap drives gain. On 1.2M
  buys the victim rate rises from ~0 (bottom half) to 27 (decile 9) and 72 per mille (decile 10,
  >= 0.31% of MC); Spearman with gain 0.38 vs 0.33 for raw SOL. Holds; it is phi without the fee term.

## Ground truth
Labels come from real attacks, not a rule. Detector = H1/H2 of Heimbach, Solmaz, Oz, Ferreira Torres
(2026, arXiv 2609.28115) with Solana parameters: legs up to two leader rotations (8 slots) apart,
back leg resells 50-101% of the front tokens, victims = same-side swaps by other wallets strictly
between. H3 entity filter (>= 3 sandwiches, >= 60% profitable, legs >= 25% of the wallet's swaps).
Why cross-slot: the paper finds 96% of Solana sandwiches span slots after the Oct 2025 delistings;
our first same-slot detector found zero tight triples in 400 slots, confirming it.

## Findings (sample 1: ~4,000 slots, 2026-10-14; sample 2: 24 h fleet pull, pending)
- **Attack economics are flow-based, not per-victim.** Median PumpSwap sandwich has 2 victims and
  3.1 SOL of victim flow on a 95 SOL pool; a single 0.6 SOL buy on that pool cannot pay the 2.4%
  round-trip fee. The attacker front-runs a burst, not a transaction.
- **phi = impact / round-trip fee hurdle** is the right fee-normalisation: victim rate climbs from
  1.0 per mille (phi < 0.01) to 55 per mille (phi 1-3), 55x. Single-feature AUC 0.83 vs size 0.76,
  slippage slack 0.61, app (router) exposure 0.61.
- **Prior same-side pool flow** (8 slots, relative to reserve) is the strongest feature: 0.3 to 93
  per mille across its range among phi > 0.3 swaps.
- **Scalpers are picked less, but not because of fees**: wallets with >= 31 trades in the prior
  ~minute are sandwiched at 22 per mille vs 50 for first trades (phi > 0.3). High priority fee by
  itself does not protect (100k-1M micro-lamports/CU is the most-hit band, the trading-app default).
- **Jito tip helps, jitodontfront does not**: tip 8 vs no tip 38 per mille (phi > 0.3); the
  dontfront flag 20.5 vs 22.6 overall, consistent with cross-slot attacks bypassing bundle rules.
- **Fee tier**: PumpSwap 120 bps pools 123 per mille (phi > 0.3) vs 18 on 125 bps pump curves.

## Filter (v1)
LightGBM on 20 features (phi, slack, MESP, size, depth, fee, flow, activity, route, fees/tips),
time split 60/40. Test AUC 0.956, AP 0.22; threshold flags 2% of swaps at 24% precision, 39% recall
(base rate 1.1%). Live session 1: precision 23.6%, recall 27.8%, lift 25x.

| score | AUC | AP | prec@1% |
|---|---|---|---|
| size | 0.762 | 0.060 | 0.125 |
| slippage slack | 0.566 | 0.015 | 0.054 |
| app exposure (Heimbach) | 0.615 | 0.021 | 0.084 |
| standalone MESP | 0.682 | 0.057 | 0.139 |
| phi | 0.829 | 0.049 | 0.078 |
| GBM full | 0.956 | 0.222 | 0.272 |

## Novel Contributions (candidate)
1. **Fee-normalised exposure phi** and slack-capped MESP on exact CP math with per-pool fees.
   Beats: size, slippage and app-level baselines (table). Test: AUC/AP on held-out time split.
2. **Victim selection model for cross-slot sandwiches**: Heimbach et al. measure who is attacked
   (apps, validators) and leave the victim-selection optimisation open; we model it. Beats: app
   exposure baseline. Test: held-out day, precision at fixed flag share.
3. **Flow-target finding**: attackers front-run pool flow bursts, so a per-transaction MESP
   underestimates exposure; aggregate-flow MESP is the open mechanism to build next.
Honest assessment: (1) is incremental over standard CP sandwich math; (2)+(3) are the publishable
delta if they hold on the 24 h sample and a second day.

## Open
- Aggregate-flow MESP (front-run size optimal against expected flow) as a feature, vs phi.
- Mayhem / fee-exempt pump curves (virtual SOL < 1, fee 0): separate regime, phi explodes.
- Leader effect in the current era; evasive (transfer-linked) sandwiches not yet detected.

## Filter v3 (24 h, 2026-10-13/14, 216,000 slots, 7.9M swaps; screener view)
Held-out last 40% of the day (1.0M swaps, 25,054 entity-filtered victims; negatives subsampled 30%
and weighted back). Deployed live 2026-10-14 20:46 UTC; threshold flags 2% at 18.8% precision,
49.6% recall.

| score | AUC | AP | prec@1% | rec@5% |
|---|---|---|---|---|
| size (SOL) | 0.745 | 0.025 | 0.049 | 0.245 |
| slippage slack | 0.536 | 0.008 | 0.016 | 0.052 |
| app exposure (Heimbach) | 0.656 | 0.017 | 0.044 | 0.177 |
| standalone MESP | 0.653 | 0.021 | 0.045 | 0.279 |
| phi | 0.834 | 0.032 | 0.063 | 0.180 |
| pool attack history | 0.801 | 0.084 | 0.145 | 0.474 |
| GBM without pool history | 0.964 | 0.210 | 0.237 | 0.750 |
| GBM full | 0.970 | 0.226 | 0.249 | 0.786 |

Stable from 4 k slots to 5 h to 24 h: slippage alone is near chance, phi is the best single swap
property, pool flow and pool attack history are the two strongest features (gain 620k / 607k).
