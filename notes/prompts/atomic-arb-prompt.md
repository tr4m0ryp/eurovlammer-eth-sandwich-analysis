# Prompt: Build the atomic cross-pool arbitrage engine (flash-loan front)

Hand this to a build agent to design + build the **atomic arbitrage** strategy that replaces the
sandwich front with a flash loan. This is a build-to-production addition to `~/projects/trenches-eth`
(see `README.md` + `CLAUDE.md`); ship in stages and gate every live send behind the operator.

## Why atomic arb (not a sandwich + flash loan)

A flash loan is **borrowed and repaid within one transaction** — atomic. A sandwich is **two
transactions** (front-run → victim → back-run), so a flash loan cannot span its two legs. Atomic
arbitrage is the natural home for the loan:

```
one tx:  flashBorrow(10 ETH)  →  buy token on pool A (cheap)  →  sell token on pool B (expensive)  →  repay 10 ETH + fee
```

The profit is the cross-pool price dislocation, captured atomically. The front (10 ETH) is borrowed;
your bankroll (~0.09 ETH) covers only gas + bribe + the flash-loan fee. Balancer charges **0%** and is
the preferred lender.

## Repo context (read these first)

- `README.md`, `CLAUDE.md` — conventions + go-live gates.
- `notes/sandwich-methodology.md` — the cost/EV model (EIP-1559 gas + bribe, the `WIN_RATE × CAPTURE`
  discount, the guard-slack distribution findings). The arb engine reuses the *cost model* but NOT the
  victim/settlement framing.
- `src/eth_mev/` — the existing V3 pipeline (decode → exposure → allocator → dashboard).

## Reuse (do NOT rewrite these — import/adapt them)

| Module | Reuse for arb |
|---|---|
| `ingest/rpc.py` → `Rpc.call`, `Rpc.batch_calls`, `Rpc.call_view`, `Subscriber.messages` | read pool state + stream blocks; `batch_calls` (chunked ≤3 for drpc) is exactly how you read N pools in one round-trip |
| `chain/v3_pool.py` → `fetch_pool_states_bulk(rpc, pools)`, `encode_selector`, `decode_slot0` | read slot0/liquidity/fee/token0/token1 for every candidate pool in one batch |
| `metrics/v3_math.py` → `exact_input(...)`, `output_to_input(...)`, `sqrt_ratio_x96`, `tick_at_sqrt_ratio`, `amount0_delta`, `amount1_delta` | the swap-leg arithmetic for the buy/sell legs (already exact + tested) |
| `chain/v3_constants.py` | Factory/Pool ABI fragments, FEE_TIERS, WETH |
| `chain/tx.py` → `normalized_tx`, `effective_gas_price` | tx field extraction + gas |
| `exec/sign.py` → `load_account`, `sign_tx` | signing (eth-account) |
| `exec/flashbots.py` → `FlashbotsRelay.send_bundle`, `live_gates_met` | gated bundle submission (reuse verbatim) |
| `exec/simulate.py` → `eth_call`, `Simulator.gate_route` | pre-send `eth_call` simulation of every arb |
| `engine/v3_allocator.py` → the cost model (`gas_cost_wei`, `landing_cost_wei`, `flash_fee_wei`) | port the EIP-1559 + flash-fee cost accounting; replace the sandwich commit/resolve with arb entry/exit |
| `serve/{hub,server}.py` + `serve/page/*` | dashboard shell (snapshot/SSE/wallet) — reuse the shape, add an "arb" panel |

## New to build

### 1. Arb detector (`metrics/arb.py` or `engine/arb_detector.py`)

Given the pool states for a token's pools (or a pool + its numeraire path), find dislocations:
- Effective price in each pool from `sqrt_price_x96` (`(sqrt/2^96)^2`, token1 per token0).
- For a candidate arb: compute the buy leg output and the sell leg input via `v3_math.exact_input`,
  subtract both pool fees and the flash fee, and the gas/bribe. Return only positive net.
- Optimal size: binary-search or golden-section over the borrow amount, bounded by pool liquidity and
  the loan cap (`MEV_FLASH_LOAN_ETH`). Reuse the golden-section pattern from `metrics/v3_exposure.py`.
- Universe: a token's pools across fee tiers (100/500/3000/10000 bps) and across V2 + V3 + forks
  (V2 price from `reserve1/reserve0`; reuse `metrics/curve.py` if you add V2 legs).

### 2. Flash-loan provider integration (`exec/flash_loan.py`)

- **Balancer V2** (recommended, 0% fee): Vault `0xBA12222222228d8Ba445958a75a0704d566BF2C8`,
  `flashLoan(address recipient, address[] tokens, uint256[] amounts, bytes userData)` — selector
  `0x5c38449e`. Callback: `receiveFlashLoan(address[] tokens, uint256[] amounts, uint256[] feeAmounts, bytes userData)`.
- **Aave V3** (fallback, 0.05%): Pool `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`,
  `flashLoanSimple(address receiver, address asset, uint256 amount, bytes params, uint16 referralCode)` —
  selector `0x42b0b77c`. Callback: `executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes params)`.
- Verify the exact selectors with `web3.keccak` (never hardcode from memory without checking).

### 3. Flash-loan executor contract (Solidity — the one genuinely new artifact)

A flash loan needs a **callback contract** that receives the borrowed tokens, does the two swaps, and
repays. Write a minimal Solidity contract (deployed once, reusable):

```solidity
// sketch only — verify against the provider's actual interface
contract ArbExecutor {
    function executeArb(address vault, address[] calldata tokens, uint256[] calldata amounts,
                       bytes calldata arbParams) external {
        IVault(vault).flashLoan(address(this), tokens, amounts, arbParams);
    }
    function receiveFlashLoan(address[] calldata tokens, uint256[] calldata amounts,
                              uint256[] calldata feeAmounts, bytes calldata userData) external {
        // decode arbParams: poolA, poolB, direction, amountIn, minOut
        // 1) approve + swap on poolA (buy)
        // 2) approve + swap on poolB (sell)
        // 3) repay token + fee to the vault (transfer token back)
    }
}
```

Deliverables: the `.sol` source, the ABI JSON, and the `exec/build.py`-style Python that encodes the
`executeArb` calldata. The Python side only signs and submits one tx to the executor with the arb
parameters; the loan + legs happen inside the contract.

### 4. Arb runner (`run_arb.py`)

`newHeads` → for each block, batch-read pool states → detect positive arb → `eth_call` simulate → if
net > threshold, build the executor calldata → `FlashbotsRelay.send_bundle` (gated). Dry-run by
default: detect + log + simulate, but never send until `LIVE=1` + funded key + sign-off.

## Decisions locked (do not re-litigate)

1. **Balancer first** (0% fee), Aave as fallback (0.05%).
2. **Atomic single-tx arb** only — no cross-tx sandwich/flash-loan hybrids (they're impossible by the
   loan's atomicity).
3. **Reuse the existing V3 math + batch RPC + gated relay**; do not rewrite `v3_math` or `Rpc`.
4. **Go-live gates unchanged**: funded dedicated hot wallet, `LIVE=1` + sign-off, exposure/drawdown
   caps, `eth_call` simulation of every arb, and a finetune report showing positive after-fee EV.

## Acceptance criteria

- `pytest` green: new tests for (a) the arb detector finding a synthetic dislocation, (b) the buy/sell
  leg math via `v3_math.exact_input`, (c) the executor calldata encoding round-trips, (d) the flash-fee
  cost model.
- A dry-run `run_arb.py` that streams blocks, detects dislocations, and logs/serves them (reuse the
  dashboard) without sending.
- The Solidity executor compiles (if a compiler is available) or its ABI/calldata is verified against
  the provider interface.
- A short note `notes/atomic-arb.md` documenting the detector, the executor, and the honest EV math
  (net = sell_out − buy_in − pool_fees − flash_fee − gas − bribe), including the go/no-go stance.

## Stop and ask before

- Any live transaction/bundle (needs funded keys + explicit authorization).
- Deploying the Solidity executor to mainnet (costs gas; confirm first).
- Committing to a paid RPC/relay or a new lender.
