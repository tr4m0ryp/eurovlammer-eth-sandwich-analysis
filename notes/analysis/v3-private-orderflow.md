# V3 + private-order-flow sandwich analysis (live-production path)

How a V3 (concentrated-liquidity) and private-order-flow sandwicher actually works, and what is
reachable in a dry-run vs. what needs keys/a builder relationship. Verified against live mainnet in
this session (2026-10-16).

## (a) Uniswap V3 concentrated-liquidity sandwiches

**Built and verified here.** `src/eth_mev/metrics/v3/tick_math.py` implements the exact V3 tick/price
arithmetic (`getSqrtRatioAtTick` computed *exactly* as `floor(Q96·√1.0001^tick)`, no dependence on the
hex-constant table), the `amount0/amount1` deltas, and `exact_input`/`output_to_input` swap iteration
across tick boundaries. `src/eth_mev/metrics/v3/exposure.py` computes the sandwich MESP by golden
section over the front-run size, evolving pool state through **front → victim → back**.

Key result (pinned by unit tests): a victim buying with 10% slippage room on a pool their 100-ETH
trade moves ~5% yields **MESP ≈ 0.48 ETH on a 5-ETH front** — a coherent, positive, fee-net result.
The V3 differences from V2 that matter:

- **Liquidity is a step function of tick.** Profit is piecewise, not a smooth `x·y=k` curve; crossing a
  tick flips the active `liquidity` by `liquidityNet`. The exposure still golden-sections fine within a
  tick range, but must walk the pool's **tick bitmap** (`ticks()` / `liquidityNet`) to be exact.
- **The victim's guard is `amountOutMinimum`** in `SwapRouter02.exactInputSingle`, the direct V3
  analogue of V2's `amountOutMin`. Most V3 volume routes through aggregators (1inch/Matcha) that
  encode the guard in aggregator calldata the router decoder doesn't see — so the *direct* V3 router
  universe is what the decoder captures, same as V2.
- **Fees are per-tier (0.01/0.05/0.3/1%)** and apply on the input side; the sandwich pays the fee twice
  (front + back), so the victim's price impact must exceed ~2× the tier fee to be profitable — which is
  exactly why only high-slippage (large-relative-to-liquidity) victim swaps are sandwichable.

## (b) Private order flow / builder relationships

Verified reachable endpoints:
- `relay.flashbots.net` — `eth_sendBundle` **requires an `X-Flashbots-Signature`** (a builder-signed
  message). Confirmed live: `{"error":{"code":-32600,"message":"signature is required"}}`.
- `mev-share.flashbots.net` — **private-order-flow SSE stream** (searchers see users' private txs
  before inclusion and may back-run them). This is the mechanism that *recovers* the visibility hidden
  by the public-mempool gotcha.

What each path needs, and the dry-run vs. live split:

| Path | What it does | Needs (for live) | Dry-run now? |
|---|---|---|---|
| Public mempool (built) | see public pending V2/V3 swaps | nothing | ✅ (thin signal) |
| MEV-Share back-running | see *private* user txs, back-run them | MEV-Share subscription is public; `eth_sendBundle` needs a key | ✅ stream (send = gated) |
| Flashbots/builder `sendBundle` | land a bundle in a builders' block, ahead of others | funded searcher key + builder signing key + builder relationship | ❌ (keys gated) |
| Builder relationship (e.g. a builder's private relay) | priority inclusion / order-flow auction | a direct agreement — not a public endpoint | ❌ |

**Honest read:** the *visibility* problem (public mempool only) is solved by subscribing to the
MEV-Share SSE stream — that part is reachable in a dry-run. The *competitiveness* problem (actually
landing ahead) is not: it requires a funded searcher key, a builder signing key, and either Flashbots
relay acceptance or a direct builder relationship — all gated behind keys the operator has not yet
provided, and none of it changes the fundamental economics: `WIN_RATE` (P(you land ahead)) stays
unknowable offline and is the dominant uncertainty.

## Verdict

- V3 sandwich *detection + exposure math*: **built, exact, tested** — a real, honest step beyond V2.
- Private-order-flow *visibility*: **reachable in dry-run** via the MEV-Share SSE stream.
- Actually *landing* ahead (the profitable half): **still gated** on funded keys + a builder
  relationship + a measured `WIN_RATE`. This report does **not** change the overall **NO-GO-for-live**
  stance from `notes/reports/finetune-report.md`; it extends the engine's *visibility* and *V3 coverage*, not
  its *edge*.
