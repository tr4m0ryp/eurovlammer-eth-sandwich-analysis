# trenches-eth

> **Research and analysis only.** This repository was built for a thesis on the analysis of
> crypto-market activity, specifically **how exposed ordinary Ethereum DEX traders are to sandwich
> attacks** and how much of that exposure is visible from public data. It measures, replays and
> reports. It is **not** a trading product, it is **not** operated against real users, and every
> entry point runs as a **dry-run that sends nothing**.

## What the project studies

A **sandwich attack** is a form of MEV (maximal extractable value, profit from ordering
transactions): an attacker sees a victim's pending swap, trades just before it to move the price,
and trades back just after it, pocketing the slippage the victim had allowed. The victim's
**slippage guard** (`amountOutMin` / `amountOutMinimum`) caps how much can be taken.

The thesis question is empirical: **how much value is exposed, how much is actually extracted, and
how much of it can an outside observer even see?** The repository answers it with three instruments:

- **Exposure measurement.** For every observed swap, compute the maximum extractable sandwich
  profit (MESP) given the pool state and the victim's guard. Constant-product pools (Uniswap V2)
  use `x·y=k`; concentrated-liquidity pools (Uniswap V3) use exact tick math.
- **Ground truth.** Label real sandwiches from confirmed blocks by inclusion order
  (mev-inspect style: one actor swaps before **and** after a victim in the same block).
- **Counterfactual replay.** Feed the measured opportunities through a capital-allocation model and
  ask whether a hypothetical extractor would break even after gas, builder bribes and lost races.

## Findings so far

Full write-ups live in `notes/`. The headline results:

- **Visibility is the bottleneck, not the filter.** On V3, about **96%** of swaps route through
  aggregators or private routers whose guard is not readable from standard router calldata. Only
  **4.1%** expose a decodable guard, half of those set **no guard at all**, and **0.7%** end up as
  positive-exposure targets (`notes/analysis/strict-filter-analysis.md`).
- **Public V2 router traffic is thin:** roughly **0.1-0.8%** of mainnet block transactions, and
  0 of 323 pending transactions in a 55 s window (`notes/reports/finetune-report.md`).
- **V3 exposure is real but concentrated.** A victim with 10% slippage room moving a pool ~5% leaves
  about **0.48 ETH** extractable on a 5 ETH front (`notes/analysis/v3-private-orderflow.md`).
- **Counterfactual verdict: NO-GO.** Under measured win rates, bribes and capture fractions the
  replayed strategy does not show positive expected value; the predecessor study on Solana measured
  it negative as well.
- **Cross-pool arbitrage** dislocations exist (a 2% same-pair gap nets ~1.34% before gas), but their
  frequency has not been shown to clear costs (`notes/analysis/atomic-arb.md`).

## How it works

```
mempool / newHeads ─▶ chain decoding ─▶ exposure metrics ─▶ screen + risk gate ─▶ allocation model
     (ingest)        (V2, V3, routers)   (MESP per swap)     (strict targets)      (counterfactual P&L)
                                                                     │
confirmed blocks ─▶ sandwich labeller (inclusion order) ─────────────┴─▶ settlement ground truth
                                                                     │
                                         DuckDB / Parquet store ◀────┴─▶ dashboard (snapshot + SSE)
```

Each stage is a package under `src/eth_mev/`:

| Package | Role |
|---|---|
| `ingest/` | JSON-RPC over aiohttp: paced HTTP calls, WebSocket subscriptions, the `newHeads` driver, mempool ingest |
| `chain/` | Transaction normalisation and swap decoding: `v2/`, `v3/` (router, UniversalRouter, embedded calls, logs), `routers/` (aggregators, private routers) |
| `metrics/` | `v2/` curve + golden-section exposure, `v3/` tick math + exposure, `sandwich.py` detection, `arb.py` cross-pool pricing |
| `screen/` | Strict filter, scorer and the settlement labeller |
| `engine/` | `allocation/v2`, `allocation/v3` counterfactual capital models and the slippage-guard estimator |
| `exec/` | The gated, **disabled-by-default** execution path kept to price realistic costs: `bundle/`, `safety/`, `arb/` |
| `store/` | Insert-only DuckDB + Parquet writer |
| `serve/` | aiohttp dashboard (passcode-gated page, JSON snapshot, SSE stream) |
| `apps/` | Runnable dry-run screeners: `v2_screener`, `v3_screener`, `arb_scanner` |

Offline tooling sits in `scripts/`: `ground_truth/collect.py` records confirmed blocks and labelled
sandwiches, and `finetune/` (backtest, sweep, report, guard percentiles) replays a recorded window.
`contracts/ArbExecutor.sol` is an uncompiled, undeployed reference sketch used to reason about
arbitrage cost structure.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest          # 100 unit tests
```

Copy `.env.example` to `.env` and set a read-only RPC endpoint (`ETH_RPC_URL`, `ETH_WS_URL`).

## Running the measurements

All three screeners are **observation-only**: they decode, score and display; nothing is signed or sent.

```bash
.venv/bin/python -m eth_mev.apps.v2_screener                 # V2 mempool exposure + settlement
.venv/bin/python -m eth_mev.apps.v3_screener --port 8080     # V3 block-level exposure funnel
.venv/bin/python -m eth_mev.apps.arb_scanner --port 8765     # cross-pool dislocation scanner
```

Record a ground-truth window, then replay it:

```bash
.venv/bin/python -m scripts.ground_truth.collect --hours 3
.venv/bin/python -m scripts.finetune.backtest --cache --db data/gt_*/live.duckdb
.venv/bin/python -m scripts.finetune.sweep --n 1500 --db data/gt_*/live.duckdb
.venv/bin/python -m scripts.finetune.report --n 1500 --seeds 20 --db data/gt_*/live.duckdb
```

Gathered data stays local under `data/` and is never committed.

## Scope and safety

This codebase exists to **analyse** sandwich exposure, not to perform it.

- Every entry point defaults to dry-run. The execution package refuses to send unless a separate
  `LIVE` flag, a dedicated funded key and every hard gate (exposure cap, drawdown kill-switch,
  slippage and deadline guards, `eth_call` simulation) are satisfied; none of these is configured.
- The execution code is retained because realistic cost modelling (gas, EIP-1559 fees, builder
  bribes, flash-loan fees) needs the same calldata and bundle shapes an extractor would use.
- Sandwich attacks harm the traders they target. Nothing here should be read as a recommendation
  to run one; the thesis conclusion is that public-mempool sandwiching is neither broadly visible
  nor profitable after costs.

## Repository layout

```
src/eth_mev/      the analysis package (see the table above)
scripts/          ground-truth collection and the offline finetune pipeline
tests/unit/       unit tests mirroring src/ (chain, metrics, engine)
notes/            methodology, analysis write-ups, reports and the original build prompts
contracts/        reference executor sketch (not compiled, not deployed)
```

## License

trenches-eth is **source-available**, licensed under the [PolyForm Noncommercial License 1.0.0](./LICENSE), **not** an OSI open-source license.

- **You may** use, modify, fork, and share trenches-eth freely for any **noncommercial** purpose, as long as you keep the copyright and `Required Notice:` lines (see [`NOTICE`](./NOTICE)) and credit *"trenches-eth by Keygraph, Inc."*
- **You may not** sell it, bundle it into a paid product, or run it as a paid/hosted service **without a commercial license**.

Copyright (c) 2026 Keygraph, Inc. Commercial licensing enquiries: see [`NOTICE`](./NOTICE).
