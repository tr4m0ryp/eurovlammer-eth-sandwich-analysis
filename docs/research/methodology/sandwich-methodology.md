# Sandwich methodology: minimal capital (0.045 ETH) on Uniswap V3

The capital-allocation framework from `~/projects/trenches-live/notes/mathematical-framework-prompt.md`
is adapted to Ethereum V3. The single biggest structural difference is that on Ethereum the *fixed
cost of landing* (EIP-1559 gas + builder bribe) is the binding constraint at tiny capital, not the
front size.

## 1. Net-profit model (EIP-1559)

A sandwich is three legs on one pool in one block: **front → victim → back**. Its extractable value is
`mesp` (golden-section MESP from `measure/exposure/v3/mesp.py`), already net of both pool fees. The cost of
landing it ahead of other searchers is:

```
cost = gas + bribe
gas  = 2 × swap_gas × (base_fee + priority_fee)      # two V3 swaps ≈ 320k gas
bribe = builder payment to order the bundle first      # the ETH analogue of the Solana Jito tip
net  = mesp − cost
```

Measured live: base fee ≈ 0.13 gwei (so `gas ≈ 0.00005 ETH` — negligible), which makes the **bribe**
the real cost. At 0.045 ETH the binding question is `mesp > bribe`, not `mesp > gas`.

## 2. Break-even commit rule

```
commit(e)  ⟺  mesp(e) > gas + bribe        (net > 0)
         AND front(e) ≤ available_capital   (hard capital constraint)
```

This replaces the Solana `p ≥ MIN_P` model-score gate: the V3 screener has no victim model, so the
selectivity lever is the **profitability of the specific swap** (`mesp`), not a learned probability.

## 3. Capital constraint + front sizing

```
wallet + Σ_{active} front ≤ bankroll  (0.045 ETH)
front_commit = min(front_opt(e), wallet)
```

The exposure already caps `front_opt` at the bankroll, so a target "wants" at most 0.045 ETH. At this
size only one position is active at a time, so there is **no concurrency** — the policy is the Solana
**greedy-by-ρ** rule: among arriving targets, commit the single highest-`mesp` one when capital is free
(and skip while a position is locked, since opportunity cost is dominated by the chance to catch a
larger `mesp` target).

## 4. Dry-run resolution (expected value, no fake luck)

There is no live sandwich-settlement feed, so resolution uses the **expected** outcome:

```
expected_gain = WIN_RATE × (CAPTURE_FRACTION × mesp − cost) − (1 − WIN_RATE) × cost
             = WIN_RATE × CAPTURE_FRACTION × mesp − cost
```

`WIN_RATE` (P(land ahead | a sandwich happened)) and `CAPTURE_FRACTION` (realised share of MESP) are
unknowable offline — both are sensitivity parameters, not learned numbers, exactly as the Solana
report concluded.

## 5. Sanity bounds (why they matter)

The reverse-engineered private-router decoders return `amount_in`/`amount_out_min` in raw token units,
and a unit/direction mismatch in the exposure can produce `mesp` up to ~1e58 ETH. The screener
therefore rejects any target with `mesp > 100 × front` or `slack ∉ (0, 100)` — a sandwich can never
extract more than ~10× its front, and no victim has a 10,000× slippage room.

## 6. The honest economics at 0.045 ETH

- `gas` is negligible at current base fee (~0.13 gwei).
- `bribe` is the binding cost; assume 0.0002–0.002 ETH depending on competition.
- `mesp = front × victim_slippage`. At front = 0.045 ETH, a 1% slippage victim yields mesp ≈ 0.00045 ETH,
  a 5% victim ≈ 0.00225 ETH.
- Therefore the strategy is: **only commit when `mesp > bribe`**, i.e. hunt the high-slippage tail of
  victims (slack ≥ ~1–5%) and let the bribe float below the per-target MESP. This is a *thin but
  structurally sound* edge at current gas prices — not a default go-live, and `WIN_RATE` remains the
  dominant uncertainty.

## 7. What moves the needle (one-knob summary)

- **`BRIBE_WEI`** is the dominant knob: raising it skips more targets but raises `WIN_RATE`; lowering it
  commits more but lands behind more often. This is the ETH analogue of the Solana Jito-tip lever.
- **`CAPTURE_FRACTION`** and **`WIN_RATE`** are resolution-sensitivity parameters, not tunables to
  optimise blindly.
- **Sanity bounds** (front cap + `mesp ≤ 100×front`) are not tunables; they are correctness guards.

## 8. Default decode approach (100% swap coverage)

When the router's exact guard isn't decodable (~74% of V3 swaps go through opaque bot routers), the
screener falls back to a **default reconstruction from the `Swap` event itself**: the event's signed
`amount0`/`amount1` give the exact input/output of that hop, and the guard is *assumed* to be a default
slippage tolerance (`MEV_DEFAULT_SLIPPAGE`, default 1%). This scores **every** V3 swap, with the exact
decoders still taking precedence when they match.

Measured live: `default_decoded` accounts for ~72% of scored swaps, pushing decode coverage from ~25%
to **~100%**, and surfacing ~13 strict targets per 8 blocks (up from 1). Caveat: the default-decoded
MESP is an *estimate* under the assumed slippage, not the victim's true guard — it is a screening
heuristic, and a live deployment would confirm each guard before committing.

## 9. Data-driven guard estimation (replaces the fixed 1%)

A fixed 1% slippage assumption is wrong. The screener now **learns** the empirical distribution of the
victim's *guard slack* — `actual_out / amountOutMinimum − 1`, the front-running room beyond the
victim's own realized impact — from the swaps whose exact guard IS decodable (`measure/guard.py`,
`GuardEstimator`), and applies a percentile of it (default median, `MEV_GUARD_PERCENTILE`) as the
default for undecodable swaps.

**Live measurement (first ~10 blocks, 7 observed single-hop decodable swaps):**
- p10 ≈ **0.0%**, p50 ≈ **0.0%**, p90 ≈ **2.0%**

Reading: the median on-chain V3 victim leaves essentially **zero** extra front-running room — most of
today's public-V3 flow is MEV bots/arbitrageurs with tight (near-exact) guards, not retail users with a
1% tolerance. The fixed 1% was **~3 orders of magnitude too optimistic** for the median. The sandwichable
room lives in the **tail** (p90 ≈ 2% and above), which is why the estimator defaults to a percentile
(and why the surfaced strict count fell from ~13 to ~3 once the real distribution was applied). The
sample is small and grows live; the estimator is the honest replacement for the fixed constant.

## 10. Persistence + percentile sweep (operational)

- **Persistence**: the learned guard-slack distribution now persists to DuckDB
  (`data/guard_estimator.duckdb`, override with `MEV_GUARD_DB`), so it survives restarts instead of
  resetting. Flushes in batches of 20 and on shutdown.
- **Percentile sweep**: `scripts/finetune/guard_percentile.py` reports the full distribution and the
  "over-estimate rate" (= the chosen percentile) — the fraction of victims whose true room is below
  the assumed slack, i.e. the revert risk of that default. Lower percentile = conservative screen;
  higher = tail-hunting.

Live run (36 observations): p10–p75 ≈ 0.0002%, p90 ≈ 1.9%, p95 ≈ 2.0%. The distribution is heavily
right-skewed — the median victim leaves ~0% room, and only the top ~10–25% leave 1–2%. So a
conservative screen (`MEV_GUARD_PERCENTILE=25–50`) assumes ~0% room and surfaces almost nothing,
while tail-hunting (`=90`) targets the 1–2% room victims at a ~90% over-estimation (revert) risk.

## 11. Why the wallet takes few targets — and the "more risk" levers

Live diagnosis (21 strict targets): only **1** had `mesp > landing_cost` (0.000264 ETH = gas 0.000064 +
bribe 0.0002); the other 20 are dust (mesp $0.02–0.25). The one committed whale (mesp 0.0025 ETH) won.

**The deeper issue — expected-value break-even, not gross.** A sandwich's expected gain is

    E[gain] = WIN_RATE × CAPTURE_FRACTION × mesp − cost   = 0.125 × mesp − cost  (at WR=0.5, CF=0.25)

so the honest break-even is `mesp > cost / 0.125 = 8 × cost`, not `mesp > cost`. The allocator's
`decide` previously used the *gross* break-even while `resolve` used the *expected* value — a
consistency bug that committed to gross-profitable but expected-negative targets. **Fixed**: `decide`
now uses the expected-value threshold, and `RISK_APPETITE` (default 1.0) scales it:
`threshold = cost / (WIN_RATE × CAPTURE × RISK_APPETITE)`.

**What "more risk" actually buys** (measured with an aggressive profile): raising the guard percentile
to p90 surfaced 38 targets, but the top MESP was only 0.000192 ETH — below even the *lowered* landing
cost's expected break-even. The aggressive commit **lost** (−0.00009 ETH). Conclusion: at 0.045 ETH
capital, committing to more (dust) targets *loses* money; the binding constraints are the fixed landing
cost and the WIN_RATE×CAPTURE discount, not the commit gate.

**Levers that genuinely earn more/faster** (ranked):
1. **Bigger front** (capital or flash-loan) — MESP scales with the front, and 0.045 ETH is below the
   economic threshold for most victims.
2. **Higher WIN_RATE / CAPTURE_FRACTION** — only measurable/improved on a real (funded) deployment.
3. **Hunt the whale tail** (`MEV_GUARD_PERCENTILE=90` + patience) — wait for the rare 0.0025-ETH victims.
4. **Lower the bribe** (`MEV_BRIBE_WEI`) — the bribe (0.0002) is 3× the gas and the largest cost.

`RISK_APPETITE` is the explicit "more risk" dial: set >1 to accept expected-negative positions for
throughput (the wallet then reports the honest expected loss).

## 12. Doubling capital (0.045 → 0.09 ETH)

Measured with the exposure math on a representative victim (100-ETH trade, 10% guard slack, 0.3% pool):

| capital | front | MESP | roi |
|---|---|---|---|
| 0.045 | 0.0450 | 0.004301 | 9.56% |
| 0.090 | 0.0900 | 0.008602 | 9.56% |
| 0.180 | 0.1800 | 0.017202 | 9.56% |

**MESP scales linearly with capital** when the front is cap-constrained (the optimal front exceeds the
bankroll, so the front = the whole bankroll and roi is constant). Doubling 0.045→0.09 exactly doubles
the MESP.

Consequence for the break-even (`mesp > 8×cost = 0.002112 ETH` at WR=0.5/CF=0.25):
- at 0.045 ETH the victim must yield roi > ~4.7% — above the observed guard-slack tail (p95 ≈ 2%),
  so almost nothing qualifies;
- at 0.09 ETH the requirement halves to roi > ~2.35% — inside the tail, so the top ~5% of victims
  become profitable.

Verdict: **doubling to 0.09 ETH is a good, cheap linear win** (roughly doubles MESP and moves the
break-even into the observed tail), but it is linear — it does not remove the dominant constraints,
which remain the landing cost (bribe) and the WIN_RATE×CAPTURE discount. A step change needs a
flash-loan front, a lower bribe, or a measured higher WIN_RATE. Capital is now configurable via
`MEV_BANKROLL_ETH` (feeds both the exposure front cap and the allocator bankroll).

## 14. Flash-loan front (10 ETH) — finetuned dry-run

Implemented the flash-loan allocator (`V3Allocator` flash-loan mode): the front is borrowed
(``MEV_FLASH_LOAN_ETH=10``) and repaid atomically, so the bankroll (``MEV_BANKROLL_ETH=0.09``) is a
**cost buffer** (gas + bribe + flash fee) + profit accumulator, not the front. New risk model:
`cost = gas(4 swaps) + bribe + flash_fee(front × 5 bps)`, a slack floor (`MEV_MIN_SLACK`), and the
expected-value break-even `mesp > cost / (WIN_RATE × CAPTURE)`.

**Measured effect** (live): the flash loan lets the front scale to ~6.8–10 ETH (vs 0.09 capped), and
the MESP for the whale target rises ~9× (0.0004 → 0.0035 ETH). The top target (mesp 0.0035, slack
0.4%) clears the 0.002624 threshold; its expected gain = 0.5×0.25×0.0035 − 0.000328 ≈ **+0.00011 ETH**.

**Where the bottlenecks now are** (post-flash-loan):
1. **Victim slack distribution** — median ~0%, p90 ≈ 1% (371 persisted observations). The flash loan
   only helps the rare high-slack tail; the median victim has no room for even a small front.
2. **Whale rarity** — the mesp>0.002624 target appears ~1 per several minutes.
3. **WIN_RATE × CAPTURE = 0.125** — even the whale's expected gain is thin; this is now the dominant
   uncertainty, not capital.

**Finetune applied**: `MEV_MIN_SLACK` lowered 0.5% → 0.1% (the 0.5% floor was rejecting the 0.4%-slack
whale that is otherwise profitable). The exposure's own feasibility check is the primary guard; the
slack floor is only a margin for the borrow/repay slippage.
