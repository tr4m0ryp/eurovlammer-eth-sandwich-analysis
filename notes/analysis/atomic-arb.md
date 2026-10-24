# Atomic cross-pool arbitrage (flash-loan front)

The strategy that replaces the sandwich front with a **flash loan** — which is where a flash loan
actually fits (atomic, one tx), unlike a two-tx sandwich.

```
one tx:  flashBorrow(10 ETH) → buy token on pool A (cheap) → sell token on pool B (expensive) → repay
```

## What's built

| File | Role |
|---|---|
| `metrics/arb.py` | `two_leg_profit` + `optimal_arb` — the borrow→buy→sell profit and the golden-section optimal borrow size, reusing `metrics/v3/tick_math.exact_input`. |
| `exec/arb/flash_loan.py` | Balancer V2 (`0xBA1222…`, 0% fee) + Aave V3 (`0x87870B…`, 0.05%) calldata encoding; selectors verified via `web3.keccak`. |
| `contracts/ArbExecutor.sol` | The flash-loan callback contract (borrow → 2 swap legs → repay, with a `NoProfit` revert so an `eth_call` reports the cycle output). Reference source; not deployed. |
| `exec/arb/build.py` | Encodes `executeArbBalancer`/`executeArbAave` with `abi.encode(tokenOut, minProfit, buyLeg, sellLeg)`. |
| `python -m eth_mev.apps.arb_scanner` | Dry-run screener: stream blocks, batch-read seed pool states, detect dislocations, serve them. Sends nothing. |

## EV math

```
profit(amount) = sell_out(amount) − amount − flash_fee(amount)
net            = profit − gas − bribe
flash_fee      = amount × fee_bps / 10_000   (Balancer 0, Aave 5)
```

Measured on the detector: a 2% same-pair dislocation on deep pools (fee 0.3% each side, Balancer 0%)
yields ≈ **1.34% net** per borrow — `profit ≈ 2% − 0.6% − 0%`. The borrow size is bounded by pool
liquidity and the loan cap (`MEV_FLASH_LOAN_ETH`, default 10).

## Status / go

- `metrics/arb.py` + `exec/arb/flash_loan.py` + `exec/arb/build.py` are **tested** (9 new unit tests,
  99 total green).
- The **Solidity executor is a reference sketch** — not compiled (no solc here) and **not deployed**.
  Deploying it to mainnet is a go-live-gated, paid step (confirm first).
- `python -m eth_mev.apps.arb_scanner` is a **dry-run**; the live send (executor calldata → `FlashbotsRelay.send_bundle`) is
  wired but disabled behind `LIVE=1` + funded keys + sign-off.

**Go/no-go**: do not deploy the executor or send until (a) the detector is validated against a fork,
(b) the executor is compiled + unit-tested, and (c) a finetune report shows the dislocation frequency
is high enough to clear gas + bribe after the flash fee. None of that is established yet.
