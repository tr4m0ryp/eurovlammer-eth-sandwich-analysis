# Prompt: Finetune the Ethereum mempool-sandwich engine to optimal settings

Hand this to the build agent to finetune the tunables, once the port in `README.md` +
`migration-analysis.md` is built and the screener has persisted settlement ground truth.

---

You are working in `~/projects/trenches-eth/` (Python 3.12, aiohttp + web3/JSON-RPC, DuckDB). Your job
is to **finetune the tunable parameters of the mempool-sandwich capital-allocation engine + risk
gates** so the simulated wallet produces the **highest risk-adjusted return** against real on-chain
settlement data. This repo's goal is a *production, profitable* bot — so the finetune's explicit job is
to get the dry-run wallet to a **positive, stable** P&L before it is allowed to go live. Tunable search
itself is read-only (it trades against historical settlement, not real capital); the go-live step that
follows it is separate and gated by the operator.

## What the system is

- A mempool watcher subscribes to geth `newPendingTransactions`, decodes **Uniswap V2 (and fork)
  swaps**, and emits **"strict" opportunities** (visible in the pending pool, gas-feasible, profitable
  after the priority fee/bribe).
- A risk gate filters each strict event by token safety (buy/sell tax, honeypot, owner/mint risk,
  liquidity age) and opportunity risk (pool depth/slippage, mempool position, base fee / priority fee).
- A wallet (`engine/allocator.py`, class `Allocator`) commits a fixed budget of ETH to accepted
  opportunities, then resolves each position against real settlement: which pending swaps actually
  got sandwiched (an attacker's swap before + after the victim's swap in the **same block**), and the
  attacker's realized profit (mev-inspect style). Outcomes are `win` / `reverted_or_outbid` /
  `no_sandwich`.

## The tunables to search

For each: file, symbol, current value, what it controls, suggested range.

**`engine/allocator.py` (class `Allocator`):**
| Symbol | Current | Controls | Range |
|---|---|---|---|
| `MIN_P` | 0.1 | model-score floor to act (the main "no-sandwich" lever) | 0.02–0.4 |
| `MIN_PROFIT_WEI` | 1e7 | minimum expected sandwich value to act | 1e5–5e9 |
| `THETA0` | 0.012 | scarce-tier expected-net threshold | 0.004–0.03 |
| `THETA_INF` | 0.004 | abundant-tier threshold (opportunity-cost floor) | 0.001–0.012 |
| `WIN_RATE` | 0.5 | P(we land ahead given a sandwich happened) | 0.1–0.9 |
| `CAPTURE_FRACTION` | 0.25 | realised share of predicted sandwich profit | 0.1–0.6 |
| `BRIBE_WEI` | 1e16 (0.01 ETH) | builder/priority-fee bribe paid per win | 1e15–1e17 |
| `BETA` | 0.004 | pool-crowding penalty | 0–0.02 |
| `ALPHA_SCARCE` | 0.5 | scarce-tier max position share of the wallet | 0.25–1.0 |

**`exec/risk.py`:**
| Symbol | Current | Controls | Range |
|---|---|---|---|
| `MAX_SLIPPAGE_FRAC` | 0.05 | position/pool-depth rejection | 0.02–0.2 |
| `MIN_COUNTERPARTY_P` | 0.02 | model-score rejection | 0.0–0.1 |
| `MAX_PRIORITY_FEE_WEI` | 50e9 (50 gwei) | priority-fee rejection (skip already-hot victims) | 1e9–200e9 |

## Ground truth

- The screener's DuckDB persists **pending swaps with their inclusion + settlement status**
  (`sandwiched` vs `clean`) and the real attacker profit. That is your ground truth for win vs
  no-sandwich.
- Alternatively the live `GET /api/snapshot?parts=strict` gives strict events with `p`,
  `expected_profit_wei`, `position_wei`, `roi`, `pool_reserves`, `base_fee`, `priority_fee`.

## Method

1. **Deterministic replay backtest** — replay a fixed historical window of strict events through
   `Allocator.step()` + `Allocator.set_settlement()` with the historical sandwich settlement, for any
   parameter vector (prefer the persisted DuckDB so runs are reproducible).
2. **Sweep** — grid first, then refine around the best cell (e.g. `optuna`).
3. **Hold out** the last ~20% of blocks for validation; don't tune on it.
4. **Report the full Pareto** of commit count (throughput), win-rate, and profit.

## Objective (ranked)

1. Maximize total realised profit (`total_profit` from `Allocator.state()`).
2. Subject to: max drawdown ≤ 25% of bankroll, and ≥ 50 committed positions in the backtest.
3. Tie-break on Sharpe-like = mean(gain) / std(gain) per resolved position.

## Constraints / gotchas

- `no_sandwich` will remain the majority outcome — the model's precision is ~25–40% even at high `p`.
  You are choosing *which* targets and *how large*, not eliminating misses.
- `WIN_RATE` is unknowable without a real deployment; treat it as a sensitivity parameter across
  several values, not a single learned number.
- Respect the exposure cap `total_locked + position ≤ bankroll` at all times.
- Do **not** change the settlement logic, the swap decoders, or the exposure math. Only tunables.
- Account for EIP-1559: the base fee + priority fee + bribe are the cost of landing ahead — this is
  the ETH analogue of the Solana Jito tip.

## Deliverable

Produce `notes/finetune-report.md` with:
1. The **optimal parameter vector** (+ env-var equivalents where applicable).
2. The backtest table (each candidate: commits, wins, reverted/outbid, no_sandwich, total profit, max
   drawdown, Sharpe).
3. The **holdout validation** showing the optimum isn't overfit.
4. A one-paragraph recommendation, including which single parameter moves the needle most.
5. A **go/no-go** for live: state the finetuned dry-run's expected P&L, its std, and the minimum
   confidence needed before the operator flips the `LIVE` gate. If the dry-run is not clearly positive
   after fees (base fee + priority fee + bribe), say so — do not recommend going live.