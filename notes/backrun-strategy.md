# Back-run / arb strategy — data-grounded feasibility (round 1)

Turn the pipeline from *front-run the victim* to something a confirmed-block bot can actually do.
Measurements below are from `data/screener.duckdb` (4 live runs, ~5.6 h, 2.27 M swaps).

## What the data says

### 1. There is no "react to the whale" edge (momentum back-run is dead)
Forward return from *just after* a buy to a few swaps later (pool-ordered), by buy size:

| buy size (SOL) | n | median immediate impact | median r(+5 swaps) | median r(+10) | mean r(+5) |
|---|---|---|---|---|---|
| 1–10 | 68,410 | +2.78% | +0.25% | +0.12% | +1.99% |
| >10 (whale) | 1,220 | +32.14% | −0.03% | −0.19% | +3.90% |
| 0.1–1 | 263,794 | +0.48% | ≈ 0 | ≈ 0 | +0.97% |
| <0.1 | — | ≈ 0 | ≈ 0 | ≈ 0 | tiny |

Reading: the *median* continuation after a whale buy is **negative** (reversion) — buying after you
*observe* a whale is statistically buying into the dump. The positive **mean** is a moonshot lottery
(a few 100x tokens), not a tradable edge. So "back-run = buy after the whale" has no median edge.

### 2. The profitable leg requires holding inventory BEFORE the whale
The sandwich's back leg (sell into the whale's +32% impact) is real, but it is the *exit half* of a
position you must already hold. Pre-positioning = the same victim-prediction problem as front-running,
just with **inventory risk** instead of **latency risk**. Our existing `p` model already predicts the
victim; reusing it to *pre-position* is the one coherent repurposing.

### 3. Cross-venue arb is thin
Only **180 mints** trade on both `pump` and `pumpswap` in this window (the rest are single-venue). The
arb surface is the pump.fun→PumpSwap migration window; narrow and contested.

### 4. Who is actually sandwiching (context)
Top-10 attacker addresses ≈ 58% of the ~10k real sandwiches; ~97% of front/back legs carry **no Jito
tip** (they run on non-Jito validators); only ~12% of victims opt into `jitodontfront`. The edge today
sits with leader access / same-slot racing, which a confirmed-block screener cannot join.

## Conclusion
There is **no free-lunch reachable edge**. The honest, reachable candidates are:

- **A. Pre-position + back-run** (repurpose the engine): use `p` to buy predicted-victim tokens, then
  sell into the *confirmed* whale (`victims` feed). Needs an exact P&L model: P(whale|high p) ×
  back-leg value − (1−P) × adverse unwind − pre-position slippage. Requires holding inventory.
- **B. Migration cross-venue arb**: 180-mint universe; needs a spread-size measurement before any bet.
- **C. Stay an analysis/observation tool** (current, honest, zero-inventory).

## Round 2 — EV of pre-position + back-run (option A) is NEGATIVE

Per-buy, binned by model score `p` (swaps joined to `victims` for "was sandwiched" and `sandwiches`
for realized `sw_profit`):

| p band | n buys | victims | precision | med sw_profit (SOL) | med victim impact | med non-victim drift (~15 swaps) |
|---|---|---|---|---|---|---|
| <.05 | 1.15 M | 4,035 | 0.4% | 0.027 | +0.23% | +0.04% |
| .05–.10 | 43,494 | 3,210 | 7.4% | 0.025 | +0.35% | +0.72% |
| .10–.20 | 44,237 | 6,059 | 13.7% | 0.018 | +0.92% | −0.13% |
| .20–.30 | 19,914 | 3,929 | 19.7% | 0.011 | +1.24% | −1.09% |
| ≥.30 | 9,416 | 2,912 | **30.9%** | 0.011 | +2.58% | −1.69% |

Overall `sandwiches` profit: median 0.0064 SOL, mean 0.0334 SOL.

EV per pre-positioned unit κ at the best band (p≥.30):
`0.309 × (+2.58%) − 0.691 × (−(−1.69%)) − fees` ≈ **−0.4%** before fees. The win capture (precision ×
impact ≈ +0.8%) is smaller than the adverse drift paid on the ~69% of positions that never get a
whale (≈ −1.7%). Every band is ≤ 0 after fees.

Root cause: the victim signal is real (`p` ranks victims ~90× the base rate) but its payoff
(+0.3–2.6% median impact) is **swamped by token drift** (±1–2% over the hold) — so holding inventory
to back-run trades signal for noise. **Option A is not a viable live strategy.** Same conclusion from
the sw_profit framing (back-leg ≈ ½ × 0.0064 SOL ≈ 0.6% on κ, still < the miss cost).

## Round 3 — cross-venue arb is migration-sniping only (option B ruled out)

For the 180 mints that trade on both `pump` and `pumpswap`, comparing executed price
(`sol/tok` ratio, unit-free) between venues within ±10 slots:

| pairs | median spread | p90 | max | >2% | >5% |
|---|---|---|---|---|---|
| 15,065 | **14.6%** | 8.7× | 491× | 93% | 79% |

The spread looks enormous, but it is **not a two-sided standing market** — it is the
pump.fun→PumpSwap *migration discontinuity*: the bonding curve's last trade (stale, near-zero curve
price) sits within a few slots of the pool's opening trade (new reference price). Those two prices are
**not simultaneously executable** (the curve is drained by the moment you could cross it), the size is
dust (median curve buy ≈ 0.08 SOL), and it is a one-shot-per-token race against every other bot.
Crossing venues also pays fees on both legs (~1–3% + gas). **Not a scalable live strategy.**

## Verdict (all reachable candidates measured)

| Strategy | Result |
|---|---|
| A. Momentum back-run (buy after whale) | ❌ median reversion |
| B. Pre-position + victim model | ❌ negative EV (≈ −0.4% at best precision; signal swamped by drift) |
| C. Cross-venue arb | ❌ migration-sniping only (one-shot, dust, contested) |

**Conclusion: a confirmed-block (lag ≥1) bot has no positive-EV sandwich/back-run/arb strategy on
this data.** The only real edge (front-run) requires pre-confirmation order-flow or validator access,
which a read-only screener cannot obtain. Recommendation: keep the repo as an MEV
observation/simulation tool (the already-deployed tuned dry-run is the right shape); a *profitable
live* engine would need a different, privileged, infra or a non-MEV product.

## Files touched
- This note (rounds 1–3). No engine code changed — measurement gates the rebuild, and the measurement
  says "don't rebuild".