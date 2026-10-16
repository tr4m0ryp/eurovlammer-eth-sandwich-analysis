# Strict-filter analysis: why 4 targets in ~1700 swaps (V3)

Measured live on the V3 scanner (mainnet, `eth.drpc.org`), after fixing the router decoder and adding
funnel instrumentation. Numbers are from a 9-block / 146-swap window; the ratios are stable and mirror
the 1700-swap observation the operator reported.

## The funnel (where swaps actually go)

| stage | count | % of decoded | meaning |
|---|---|---|---|
| V3 Swap events decoded | 145 | 100% | every V3 swap on the public chain |
| …with resolvable pool state | 145 | 100% | slot0/liquidity read OK |
| **direct router call (guard decodable)** | **6** | **4.1%** | only these have an `amountOutMinimum` we can read |
| …and `amountOutMinimum > 0` | 3 | 2.1% | half of direct calls set **no slippage guard** |
| …and MESP > round-trip fee | 2 | 1.4% | guard exists but the sandwich is still ≤ fees |
| **strict (surface)** | **1** | **0.7%** | |

## Conclusion: it is NOT a filter-parameter problem

The strict filter is not "mis-tuned". The collapse is almost entirely at the **decode-coverage** stage,
before the filter's parameters even apply:

1. **~96% of V3 swaps are invisible to the guard decoder.** They route through aggregators
   (1inch, Matcha, 0x, UniswapX, MEV bots) whose slippage guard lives in the aggregator's own
   `minReturn`-style field, not in `SwapRouter02.exactInput*`. The scanner only decodes the direct
   router, so it never sees these victims' guards.
2. **Half the *direct* calls set `amountOutMinimum = 0`** — no slippage protection at all, so there is
   no "sandwich room" by construction. Correctly rejected.

That leaves ~1% of the raw stream as strict targets. **Tuning `MIN_*`/`THETA_*`/`CAPITAL` cannot raise
this** — those knobs only choose *among* already-decoded candidates; they cannot recover the ~96% whose
guard is never decoded.

## Two decode bugs (fixed this session) were silently worsening it further

- `0xc04b8d59` was mapped to `exactInputSingle` but is actually `exactInput(bytes path,…)` — the
  multi-hop form — so those swaps threw on ABI decode and were dropped.
- `multicall(uint256,bytes[])` (the wrapper SwapRouter02 prepends on most swaps) was not unwrapped.

Both are fixed and unit-tested (`tests/unit/test_v3_router_decode.py`, 6 tests). Verified on real
mainnet: direct `exactInputSingle`/`exactOutputSingle` now decode their `amountIn`/`amountOutMinimum`.

## What actually widens reach (ranked)

1. **Decode aggregator guards** (biggest lever, ~96% of swaps). Add 1inch `AggregationRouterV5.swap`
   (`0x12aa3caf`, `desc.minReturnAmount`), Matcha/0x `transformERC20` (`0x08394347`), and UniswapX
   order `minAmountOut`. This recovers the guard for the vast majority of currently-invisible volume.
   Large ABI surface, version-versioned — scope as its own task.
2. **Fetch the real tick bitmap** (`ticks()`/`liquidityNet`) instead of `ticks={}`. The current
   single-range model *understates* MESP and rejects borderline victims. Medium effort, real gain.
3. **Fix numeraire orientation.** `_score` hardcodes `numeraire_is_token0=True`, which mis-orders the
   front/back legs for the common WETH-is-token1 pools. Small fix, correctness gain.
4. **Only after 1–3:** revisit the actual filter thresholds (`MIN_PROFIT`, front cap, `strict` gating)
   against the now-larger candidate set.

## Recommendation

Do **not** spend effort re-tuning the strict-filter knobs against the current (tiny) candidate set —
the ~0.7% rate is the honest yield of the public, direct-router V3 universe. The actionable win is
**decode-coverage** (item 1 above): recover the aggregator guards, then re-run the funnel. I can build
the 1inch/Matcha/0x guard decoder next.
