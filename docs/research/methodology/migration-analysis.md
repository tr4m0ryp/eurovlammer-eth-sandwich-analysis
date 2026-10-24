# Migration analysis — trenches-live (Solana) → trenches-eth (Ethereum)

Per-module inventory of `~/projects/trenches-live`, classified **reuse** (near-copy),
**adapt** (same role, different details), or **rewrite** (chain-specific).

Legend: ✅ reuse · 🔧 adapt · ❌ rewrite.

## Decision summary

The analytics core is chain-agnostic constant-product math and carries over almost verbatim. What
changes is everything that touches the chain: **ingest (mempool vs confirmed-block), chain decoders
(Uniswap vs pump.fun), execution (Flashbots/EIP-1559 vs Jito), and settlement (inclusion-order vs
Solana labeller).**

| Source module | Class | Ethereum target / notes |
|---|---|---|
| `tlive/metrics/curve.py` | ✅ | Uniswap V2 & forks are literally `x·y=k` — reuse verbatim. |
| `tlive/metrics/exposure.py` | 🔧 | Same golden-section MESP math. Remap the `Swap` fields: `amountOutMin`/`deadline` ≈ the victim's "limit"; Uniswap V2 only (V3 concentrated liquidity is a separate, harder model — do V2 first). |
| `tlive/metrics/features.py` | 🔧 | Keep flow/wallet/volume features; drop `cu_price`, `jito_tip`, `dontfront`, program IDs; add `gas`, `base_fee`, `priority_fee`, `block_position`, `origin`, `nonce`. Retrain the model. |
| `tlive/metrics/sandwich.py` | 🔧 | Sandwich *detection* moves to mev-inspect style (front+back around a victim in one block); the scoring math is reusable. |
| `tlive/engine/allocator.py` | 🔧 | **The centrepiece to keep.** Capital allocation + risk gates + tunables framework transfers; remap fields and the resolve/settle loop (see below). |
| `tlive/exec/risk.py` | 🔧 | Opportunity-risk gate (slippage/confidence/gas). Same shape, ETH risk fields. |
| `tlive/store/writer.py`, `consolidate.py`, `export.py` | 🔧 | DuckDB+Parquet store pattern; new SCHEMA (blocks, pending_swaps, sandwiches, victims, risk). |
| `tlive/serve/{server,hub,artifact}.py`, `page/*` | ✅ | aiohttp dashboard/SSE/snapshot — presentation is chain-agnostic, reuse. |
| `tlive/screen/scorer.py` | 🔧 | LightGBM victim model + rolling pool-flow context — reuse the pattern, retrain. |
| `scripts/finetune/{backtest,sweep,report}.py` | 🔧 | **Reusable harness.** Rewrite `reconstruct()` + settlement; keep the replay/sweep/holdout/report skeleton. |
| `research/sandwich/{label,evaluate,optimal}.py` | 🔧 | label→train→optimize pipeline blueprint; new ETH labelling. |
| `tlive/chain/programs.py` | ❌ | → Uniswap V2 Router2/Factory/Pair, V3 SwapRouter/Quoter, WETH, multicall addresses. |
| `tlive/chain/tx.py` | ❌ | Solana account-keys/jitodontfront → Ethereum tx (`from/to/value/gas/fees/calldata`, ABI decode). |
| `tlive/chain/swaps/{pump,pumpswap}.py` | ❌ | → `swaps/uniswap_v2.py` (+ `uniswap_v3.py` later): decode `Swap` events/reserves from pending txs. |
| `tlive/ingest/follow.py` | ❌ | confirmed-`getBlock` follower + forkserver → `ingest/mempool.py`: `newPendingTransactions` WS + `eth_subscribe("newHeads")` for settlement; keep the strict-order consumer pattern. |
| `tlive/ingest/rpc.py` | 🔧 | Same JSON-RPC-over-aiohttp; swap Solana methods for `eth_*`. |
| `tlive/exec/keypair.py`, `accounts.py`, `bundle.py`, `submit.py`, `template.py`, `build.py`, `extract.py`, `sandwich.py`, `safety.py` | ❌ | Jito bundle/signing/account-templates → `exec/flashbots.py` (relay sendBundle), `exec/sign.py` (eth-account), `exec/simulate.py` (forked `eth_call` / Tenderly), `exec/build.py` (swap calldata), `exec/safety.py` (ERC-20 honeypot/tax/owner checks via simulation). |
| `tlive/screen/labels.py` | ❌ | Solana labeller → ETH sandwich labeller (inclusion-order front/back detection). |
| `tlive/screen/visibility/{live,strict,tier}.py` | 🔧 | `tier` (lag/shielding) → mempool position & "can we land ahead"; keep the "strict = visible + feasible + ROI≥threshold" funnel. |

## The allocator remap (the important one)

`Allocator._decide` / `_resolve` key off these Solana fields — map to ETH:

| Solana field | Ethereum equivalent |
|---|---|
| `front_budget` (κ, lamports) | position size in wei (amountIn) |
| `mesp_budget` (π, lamports) | expected sandwich profit in wei (MESP on V2) |
| `roi = mesp/front` | profit / position size |
| `pool_freq` | pool activity / same-pool pending-swap count |
| `cu_price`, `jito_tip` | `priority_fee` / builder bribe |
| `victims[(sig,ev_index)]` settlement feed | `(block, tx_hash)` → was a sandwich's victim + attacker profit |
| `TIP_LAMPORTS` | builder bribe (ETH) |

Outcome names: Solana `win / race_loss / no_counterparty` → ETH `win / reverted_or_outbid / no_sandwich`.

## What does *not* transfer

- Solana's `jitodontfront`/`dontfront` and the "no public mempool" visibility tier — Ethereum *has* a
  public mempool, so the front-run visibility problem is replaced by a *competitive* one (priority-fee
  auction + private order flow).
- The `victims` treatment re: mempool shutdown — irrelevant on ETH.

## Suggested build order

1. `ingest/mempool.py` + `chain/tx.py` + `chain/swaps/uniswap_v2.py` (see pending swaps + reserves).
2. `metrics/exposure.py` remap + `engine/allocator.py` remap (V2 only).
3. Settlement labeller (mev-inspect style) → DuckDB ground truth.
4. `scripts/finetune/*` replay harness → run the finetune prompt (`finetune-prompt.md`).
5. `exec/` (Flashbots + simulate) last — only after the engine is tuned read-only.