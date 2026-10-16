# trenches-eth

Ethereum mempool-sandwich engine, derived from `~/projects/trenches-live` (the Solana pump.fun/PumpSwap
screener). Solana has no public mempool and Jito mitigates front-running, so the *reachable* MEV moved
to a chain with a free public mempool: **Ethereum** (EVM).

Status: **built + build-to-production wiring complete.** All eight build phases are implemented
(chain decode, mempool ingest, exposure, allocator, settlement, storage, dry-run screener, the
disabled live execution path) and `pytest` is green (28 tests). The finetune harness runs end-to-end
and the ground-truth collector is streaming confirmed blocks into `data/gt_*/live.duckdb`; see
`notes/finetune-report.md` for the honest go/no-go (currently **NO-GO by default**).

## What this repo does

1. Subscribes to pending txs (`newPendingTransactions`) — sees swaps **before** inclusion.
2. Decodes **Uniswap V2** (and SushiSwap-style forks) swaps from pending txs and computes each swap's
   sandwich exposure (golden-section MESP).
3. Filters ("strict") + risk-gates candidates, then a wallet (virtually) commits capital and resolves
   each position against real inclusion-order settlement (mev-inspect style: attacker swap before
   *and* after the victim in the same block).
4. Finetunes the capital-allocation engine against that ground truth.
5. Holds a *fully built but disabled* live path (Flashbots/relay bundles + EIP-1559 fees) that only
   ships behind the go-live gates.

## Layout

- `src/eth_mev/chain/` — Uniswap V2 addresses/ABIs + swap decoding (`constants.py`, `tx.py`, `pools.py`,
  `swaps/model.py`).
- `src/eth_mev/ingest/` — `rpc.py` (eth_* JSON-RPC over aiohttp) + `mempool.py`
  (`newPendingTransactions` + `newHeads`, strictly ordered).
- `src/eth_mev/metrics/` — `curve.py` (constant-product legs), `exposure.py` (golden-section MESP),
  `sandwich.py` (inclusion-order detection).
- `src/eth_mev/engine/` — `allocator.py` (capital allocation + risk gates, tunables via `MEV_*` env).
- `src/eth_mev/exec/` — `risk.py`, `sign.py`, `build.py`, `simulate.py`, `flashbots.py` (disabled live path).
- `src/eth_mev/screen/` — `strict.py`, `scorer.py`, `labels.py`.
- `src/eth_mev/store/` — `writer.py` (DuckDB + Parquet, insert-only + settle-later).
- `src/eth_mev/serve/` — `server.py` (aiohttp `/api/snapshot` + SSE).
- `scripts/finetune/` — `backtest.py`, `sweep.py`, `report.py`.
- `run.py` — the dry-run screener (ingest → decode → exposure → strict → risk → allocator → settle).
- `notes/` — migration analysis, finetune prompt, and `finetune-report.md`.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

## Run (dry-run, default)

```bash
export ETH_RPC_URL=https://eth.drpc.org      # free public RPC (verified)
export ETH_WS_URL=wss://eth.drpc.org         # exposes newPendingTransactions (verified)
.venv/bin/python run.py                        # serves http://localhost:8080 (snapshot + SSE)
```

The default is a **dry-run**: it watches, scores, screens, and virtually commits capital, settling
against real on-chain sandwich ground truth — but **sends nothing**.

## Finetune (offline, read-only)

```bash
# after a watcher window has persisted data/live_*/live.duckdb:
.venv/bin/python -m scripts.finetune.backtest --cache --db data/live_*/live.duckdb
.venv/bin/python -m scripts.finetune.sweep --n 1500 --db data/live_*/live.duckdb
.venv/bin/python -m scripts.finetune.report --n 1500 --seeds 20 --db data/live_*/live.duckdb
```

## Go-live (hard gates — do not bypass, do not ship live silently)

1. **Funded, dedicated bot hot wallet** — never the operator's personal key (`ETH_BOT_KEY`).
2. **Operator explicitly enables live** — `LIVE=1` *plus* written sign-off.
3. **Hard exposure cap** — `total_locked + position ≤ bankroll`; max-drawdown kill-switch;
   per-token + per-position loss limits; slippage + deadline guards on every leg.
4. **`eth_call` / forked simulation** of every new route before it is enabled live (`exec/simulate.py`).
5. **Finetune report shows clearly positive after-fee expected P&L** — a *go decision*, not a default.
   (Current status: **NO-GO** — no fresh ground-truth window, predecessor measured negative EV.)

## Safety / conventions

- No live `send`/bundle without funded keys + explicit go-ahead. Dry-run/simulate is the default.
- Never log/commit private keys. Never delete gathered data (compress instead).
- Python 3.12; ~300 lines max per file; the `exec/` live path refuses to send unless `LIVE=1` *and*
  a funded key is present.
