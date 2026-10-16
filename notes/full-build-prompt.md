# Prompt: Build + finetune the Ethereum mempool-sandwich engine (full launch)

Hand this to a build agent to do the entire build + analysis: scaffold → live mempool ingest → Uniswap
decode → exposure → capital-allocation engine → settlement → storage → replay backtest → tunable
finetune → go/no-go report. This is a **build-to-production** project (see `README.md` + `CLAUDE.md`);
ship in stages and gate every live send behind the operator.

---

You are working in `~/projects/trenches-eth/` (a fresh git repo). Your job is the **entire build +
analysis** for Ethereum sandwiching: scaffold → live mempool ingest → Uniswap decode → exposure →
capital-allocation engine → settlement → storage → replay backtest → tunable finetune → report.

**This repo is build-to-production** (it IS the live-trading successor to the Solana read-only
screener). So build the *live* execution path too — but only ship it behind the go-live gates below.
Nothing submits a real bundle until the operator provides funded keys and explicitly authorizes it.

## Read these first (they already exist)
- `README.md` — what the repo is + go-live gates.
- `CLAUDE.md` — conventions + the Solana→Ethereum delta.
- `notes/migration-analysis.md` — the reuse/adapt/rewrite table (authoritative).
- `notes/finetune-prompt.md` — the tunables, objective, method, and deliverable you must satisfy.

## Parent repo (reference, never write into it)
`~/projects/trenches-live` (Python 3.12, aiohttp, DuckDB) is the Solana screener to port from. Reuse
directly (copy, adapt): `tlive/metrics/curve.py` (constant-product legs), `tlive/metrics/exposure.py`
(golden-section sandwich MESP), `tlive/engine/allocator.py` (capital allocation + risk gates),
`tlive/serve/*` (dashboard), `tlive/store/*` (DuckDB), `scripts/finetune/{backtest,sweep,report}.py`
(replay/sweep/holdout/report), `research/sandwich/*` (label→train→optimize). Its git log + notes are
the record of every prior bug.

## Decisions already locked (do not re-litigate)
1. **Uniswap V2 first** (and forks: SushiSwap/PancakeSwap-style). V3 (concentrated liquidity) later.
2. **Build-to-production, safely:** build + `eth_call`/forked simulation now; live sends only behind the
   gates. The *live* execution path (builder bundles + signed txs) must exist and be tested, not stubbed.
3. **Model EIP-1559 explicitly**: `base_fee + priority_fee + builder_bribe` is the ETH analogue of the
   Solana Jito tip — the cost of landing ahead — and belongs in the engine's decision rule.
4. **Ground truth** = inclusion-order settlement: a victim swap is `sandwiched` when an attacker's swap
   appears both before and after it in the same block with positive atomic profit (mev-inspect style).

## Build phases (with acceptance criteria)

**0. Env.** Python 3.12 venv; `web3`, `aiohttp`, `eth-account`, `hexbytes`, `duckdb`, `pyarrow`,
`lightgbm`, `pandas`, `numpy`, `pytest`.

**1. Chain + decode.** `chain/`: Uniswap V2 Factory/Router/Pair addresses, WETH, `getReserves()`/`Swap`.
Decode a swap from a pending tx (calldata or logs) into `(pair, token0, token1, is_buy, amount_in,
amount_out, amountOutMin_limit, fee=30bps)`. Pool price = `reserve1/reserve0`; the victim's
`amountOutMin` is its slippage guard = the sandwich's room before revert. Unit-test against a known
mainnet tx + pair.

**2. Ingest.** `ingest/mempool.py`: subscribe `newPendingTransactions` (WS) AND `newHeads`/block stream
for inclusion, delivering pending swaps strictly in receipt order (keep the ordered-consumer pattern
from `tlive/ingest/follow.py`). `ingest/rpc.py`: raw `eth_*` JSON-RPC over aiohttp.

**3. Exposure.** Port `metrics/curve.py` verbatim; port `metrics/exposure.py` remapping `Swap` to V2
(buy = token-out for ETH/stable-in; sell = token-in for ETH/stable-out; `limit` = `amountOutMin`). Keep
the golden-section + `CAPITAL` cap exactly — do not rewrite the math.

**4. Engine.** Port `engine/allocator.py` + `exec/risk.py` per the field map in
`notes/migration-analysis.md`: `front_budget→position_wei`, `mesp_budget→expected_profit_wei`,
`cu_price/jito_tip→priority_fee/bribe`, `victims dict→(block, tx_hash)` settlement. Outcomes:
`win / reverted_or_outbid / no_sandwich`.

**5. Settlement + store.** A labeller that finds front+back pairs around a victim per block and records
the attacker's realized profit. Persist to DuckDB (pending_swaps, included_swaps, sandwiches, victims,
risk) using the `tlive/store/*` pattern. Sanity-check the sandwich/profit distribution (should be
top-heavy — a few bots do most of it).

**6. Screener wiring + dry-run + live path.** `run.py`: ingest → decode → exposure → strict → risk →
allocator → settle, serving the `tlive/serve/*` dashboard. **Also build `exec/`:** `sign.py`
(eth-account), `build.py` (swap calldata), `simulate.py` (forked `eth_call`), `flashbots.py` (relay
`sendBundle`). Build the live path fully but keep it **disabled** until the operator provides keys.

**7. Ground-truth window.** Run the watcher live for at least a few hours; persist the settlement
DuckDB. Confirm `newPendingTransactions` availability on your provider (free tier may not expose it;
a low-tier paid RPC or a local reth/erigon node is acceptable — verify, don't fabricate).

**8. Finetune.** Execute `notes/finetune-prompt.md` against that DuckDB: deterministic replay (reuse
`scripts/finetune/*`, rewrite only `reconstruct()` + settlement), grid sweep → refine, hold out the last
20% of blocks, report the full Pareto, and write `notes/finetune-report.md` **including a go/no-go:**
the dry-run's expected P&L + std vs the live cost of landing ahead. Do not recommend live unless the
dry-run is clearly positive after fees.

## Gotchas (do not ignore)
- `no_sandwich` stays the **majority** outcome (precision ~25–40% even at high `p`).
- **Much of Ethereum volume is private order flow** (Flashbots/MEV-Share/builders) — the engine only
  sees the public-mempool subset; do not assume full visibility.
- `WIN_RATE` is unknowable offline → report across several values, never as one learned number.
- Respect `total_locked + position ≤ bankroll`. Don't change settlement/decoders/exposure math during
  finetune — only tunables.
- ~300 lines max per file, 4/4 tree rule. Never delete data; compress instead. Never log/commit keys.

## Go-live gates (hard — do not bypass, and do not ship live silently)
1. Funded, dedicated bot hot wallet (never the operator's personal key).
2. Operator explicitly enables live (e.g. `LIVE=1` + sign-off).
3. Hard exposure cap + max-drawdown kill-switch + per-token/per-position loss limits.
4. `eth_call`/forked simulation of every new route before enabling it live.
5. Finetune report shows clearly positive after-fee expected P&L (a go decision, not a default).

## Deliverables
1. A tested, importable package (pytest green), plus a runnable dry-run screener (`run.py`).
2. The full (disabled) live execution path in `exec/` with its go-live gates.
3. The persisted ground-truth DuckDB.
4. `notes/finetune-report.md` from phase 8, including the go/no-go recommendation.
5. Updated `README.md` (how to run + how to go live responsibly).

## Stop and ask before
- Any live transaction/bundle (needs funded keys + explicit authorization).
- Committing to a paid data provider or hardware (node) — confirm choice + cost first.
- Enabling `LIVE` or relaxing any safety gate.