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

Both are fixed and unit-tested (`scripts/checks/onchain/v3/test_v3_router_decode.py`, 6 tests). Verified on real
mainnet: direct `exactInputSingle`/`exactOutputSingle` now decode their `amountIn`/`amountOutMinimum`.

## What actually widens reach (ranked)

Measured and **implemented** this session. The real cause of the low strict-rate is **decode
coverage**, not filter thresholds. Decode coverage went **~6% → ~18.4%** (3×), and surfaced strict
targets went **1 → 5 per ~8 blocks** in the live funnel.

### Implemented decoders (in decode order)
1. **Uniswap V3 router by selector** (not whitelisted `to`) — `exactInputSingle`/`exactOutputSingle`/
   `exactInput`/`exactOutput`/`multicall(uint256,bytes[])`/`multicall(bytes[])`. The biggest single
   win: MEV-bot/aggregator routers (`0xdfa8ead912…` ~14%) reuse the Uniswap ABI with a non-Uniswap `to`.
2. **Uniswap UniversalRouter `execute`** (`0x24856bc3` + `0x3593564c`) — walks the `commands` byte
   string for `V3_SWAP_EXACT_IN/OUT` (0x00/0x01), returns `amountOutMin` + first-hop path. +3.3%.
3. **Aggregators** (1inch `swap`/`unoswap`, 0x/Matcha `transformERC20`/`sellToUniswap`, Paraswap
   `simpleSwap`/`multiSwap`) — built + tested, but **~0.5% live traffic** (classic aggregators are
   nearly absent from the V3 flow).
4. **Generic embedded-standard-swap scan** — scans raw calldata for an embedded
   `exactInputSingle`/`exactOutputSingle`/V2 `swapExact*` selector at any offset and decodes the ABI
   that follows. Recovers the guard from opaque wrappers that internally call the standard Uniswap ABI.
   +0.7%.

### The hard floor (genuinely undecodable)
The two biggest remaining "unknown" routers — `0x278d858f…`/`0x42e3dcf3` (74 txs) and
`0x51c72848…`/`0x771d503f` (45 txs) — return `UNKNOWN` from every public signature DB **and contain
zero embedded standard swap selectors**. They are fully proprietary MEV-bot/private routers; no public
ABI and no recoverable guard. These (plus ERC-4337 `handleOps` user-operations) are the remaining
~81% — not decodable without reverse-engineering private bytecode.

### The remaining honest truth
Of the recovered guards, **~90% are still `amountOutMinimum == 0`** — victims set no slippage
protection, so there's no margin to sandwich. Strict targets are rare because **most on-chain V3
victims genuinely have no sandwichable slippage margin** — a correct answer, not an implementation
shortfall.
