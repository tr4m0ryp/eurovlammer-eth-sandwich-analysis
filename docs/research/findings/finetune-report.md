# Finetune report: Ethereum mempool-sandwich engine

Backtest replays the persisted strict event stream (rebuilt offline: `interesting` → budget-capped V2
exposure → ROI≥1%) through the live `Allocator` plus the three tunable opportunity-risk gates, settled
against the run's real inclusion-order settlement (mev-inspect style: an attacker swap before **and**
after the victim's swap in the same block). Selection knobs are tuned on the first 80% of blocks; the
last 20% is held out.

**Status: ground-truth collection is LIVE.** The corrected collector is streaming confirmed blocks
into `data/gt_*/live.duckdb` (3-hour run, started at build time) — decoding real V2 `Swap` events,
resolving pool reserves, capturing the victim's `amountOutMin` guard on direct V2 Router02 calls, and
running inclusion-order sandwich detection. Until that window completes, the numbers in §2 are a
harness *functionality* check on synthetic data — **not a profit estimate** — and the go/no-go (§5)
is written from the measured predecessor evidence + first principles, not a fresh ETH window.

## 0. Live-ingest verification (phase 7)

- **Provider:** `https://eth.drpc.org` (HTTP) / `wss://eth.drpc.org` (WS), free tier.
- **`newPendingTransactions` availability: CONFIRMED** (hash-only payloads, standard geth), and the
  settlement side (`newHeads` → block → receipts → `Swap` event decode) was verified against real
  blocks: **8+ V2 swap events decoded from a single recent block**, including a direct Uniswap V2
  Router02 buy decoded to `(kind=in, amount=9.04e15, amountOutMin=3.70e20, deadline=...)`.
- **Signal density is genuinely thin:** in a 55s pending window, 0 of 323 pending txs were V2 Router02
  calls; direct V2 Router02 traffic is ~0.1–0.8% of mainnet block transactions (V3 + aggregators +
  private order flow dominate). The engine only sees this public subset; do not assume full visibility.
- **Correctness fixes made during phase 7** (each verified against real data): (a) case-insensitive
  router-address matching (real tx `to` fields are lower-cased; the decoder previously skipped them),
  (b) `victims` table column-count alignment, (c) uint256 swap amounts cast to float64 so pyarrow
  never overflows on high-decimal tokens, (d) the dry-run screener's fee resolver fed `str(float)`
  to `int()` (startup crash), fixed + regression-tested.

### Measured sandwich rate on the public V2 subset (this repo's own data)

Across **28 confirmed blocks** collected live (a completed short run: 8 blocks / 69 V2 swap events,
plus a 20-block checkpoint: 117 swap events): **0 sandwiches detected**, and only **2 of 69 swaps
(≈2.9%) are direct Uniswap V2 Router02 calls with a captureable `amountOutMin`**. The rest are
aggregator-routed V2 swaps (1inch/matcha/etc.) whose slippage guard lives in aggregator calldata the
V2 decoder does not see. This is the honest, *measured* reality of the V2-first universe on mainnet
today: direct Router02 victims are ~1/block, and public-mempool V2 sandwiches are rare to absent —
consistent with the prompt's "much of Ethereum volume is private order flow" gotcha. A 3-hour window
extrapolates to ~150 direct-router victims, of which the parent's ~8–10% sandwich rate implies only
~12–15 sandwiched victims — too few for a robust ≥50-commit finetune, and every one of them a
slippage margin that an EIP-1559 bid must still clear.

## 1. Optimal parameter vector (selection knobs)

The harness converges on (synthetic corpus; treat as a *mechanism* check, not a production setting):

- `MIN_P` = `0.07`
- `MIN_PROFIT_WEI` = `9.6e8`
- `MAX_SLIPPAGE_FRAC` = `0.14`
- `MIN_COUNTERPARTY_P` = `0.08`
- `MAX_PRIORITY_FEE_WEI` = `1.6e9`
- `THETA0` = `0.024`, `THETA_INF` = `0.004`
- `BETA` = `0.01`, `ALPHA_SCARCE` = `0.59`

Env equivalents (allocator + risk gate): `MEV_MIN_P`, `MEV_MIN_COMMIT_PROFIT`, `MEV_MAX_SLIPPAGE`,
`MEV_MIN_P`, `MEV_MAX_PRIORITY_FEE`, `MEV_THETA0`, `MEV_THETA_INF`, `MEV_BETA`, `MEV_ALPHA_SCARCE`.

Do **not** ship these as-is. They are what the (empty-ish) synthetic window produced; the real sweep
must run on hours of mempool + settlement data.

## 2. Backtest table (synthetic corpus, mean over 8 seeds @ WR=0.5 / capture size-dep / bribe 0.01 ETH)

| candidate | commits | wins | outbid | no_sw | profit (ETH) | profit ± | max DD | Sharpe |
|---|---|---|---|---|---|---|---|---|
| default (train) | 0 | 0 | 0 | 0 | +0.0000 | 0.0000 | 0.0% | +0.000 |
| best → holdout | 0 | 0 | 0 | 0 | +0.0000 | 0.0000 | 0.0% | +0.000 |

The synthetic corpus's unbounded-slippage victims are correctly rejected by the slippage gate
(`front/pool_depth > MAX_SLIPPAGE_FRAC`), so it commits ~0. On the parent's *real* Solana window the
same harness commits 50–90 positions per run — the difference is data, not code. A **direct replay
smoke test** with realistic rows (deep pool, positive ROI, `p=0.3`) produced **80 commits, 7
sandwiched, 3 wins / 4 outbid / 13 no_sandwich, −0.17 ETH** — proving the full commit→resolve→settle
lifecycle and the three outcome buckets (`win` / `reverted_or_outbid` / `no_sandwich`) work as designed.

## 2b. Throughput vs profit (Pareto): MIN_P (illustrative)

The `MIN_P` selectivity knob trades commit count against precision exactly as in the Solana report:
the "no_sandwich" bleed — the largest recurring loss — shrinks as `MIN_P` rises, at the cost of
throughput. Precision plateaus near ~30–40% even at high `p` (the parent measured 30.9% at `p≥0.3`).

## 3. Resolution sensitivity (WIN_RATE is unknowable offline)

`WIN_RATE` — P(we land ahead *given* a real sandwich lands on our victim) — is **not** measurable with
historical data and is the dominant uncertainty. It must be treated as a sensitivity parameter across
several values, never a single learned number. The parent's report showed expected P&L crosses ~zero
around WR 0.3–0.5 and turns clearly positive only above ~0.6–0.7. On Ethereum the floor is *higher*: a
public-mempool bot bids for inclusion ahead of every other searcher **and** against private order flow
it cannot see, so the honest prior on `WIN_RATE` is pessimistically low until a small live sample
measures it.

## 4. What moves the needle

The single most reliable lever is the model-score floor `MIN_P` (the "no_sandwich" lever): raising it
cuts the adverse-unwind bleed and lifts hit-rate toward ~40%, exactly as the predecessor found. On
Ethereum, two *new* levers join it and are first-order:
- the **EIP-1559 cost of landing ahead** (`base_fee + priority_fee + builder_bribe`), which is the ETH
  analogue of the Solana Jito tip and directly subtracts from every winning sandwich;
- **private-order-flow blindness** — the engine cannot see Flashbots/MEV-share volume, so the effective
  venue is a competitive subset, not the whole mempool.

## 5. Recommendation + go/no-go

**NO-GO by default.** This is not a placeholder — it is the honest conclusion.

1. **No fresh ground truth yet.** The persisted DuckDB is synthetic (a harness check), not hours of
   live settlement. Finetuning against it cannot produce a real expected P&L, and I will not fabricate
   one.
2. **The predecessor's measured outlook is negative.** `~/projects/trenches-live/notes/backrun-strategy.md`
   measured, on real Solana data, that *no* reachable confirmed-block strategy (momentum back-run,
   pre-position + victim model, cross-venue arb) had positive EV after fees. Ethereum front-running is
   a *different* (latency-competitive) game, but the structural warnings — signal swamped by drift,
   precision ~25–40%, profit dominated by rare whale targets — carry over verbatim.
3. **Public-mempool V2 signal is thin.** The live probe saw 0 V2 Router02 calls in 323 pending txs.
   A sandwich engine competing *only* on the public subset, against builders with private order flow
   and lower latency, faces a steep `WIN_RATE` and an EIP-1559 bidding cost on every attempt.

**To reach a *real* go/no-go:** (a) run the verified watcher for **≥ a few hours** against drpc (or a
low-tier paid RPC — confirm the choice/cost with the operator first) to persist an inclusion-order
settlement DuckDB; (b) run `scripts/finetune/report.py` against it; (c) only then evaluate whether the
after-fee dry-run P&L is clearly positive **and** `WIN_RATE` measured on a small live sample ≥~0.6.
Until both hold, do not flip the `LIVE` gate.

## Files

- Engine: `eth_mev/{onchain,measure,strategy,app}/`.
- Screener: `python3 . v2` (dry-run by default; `LIVE` + funded keys required for the live path).
- Finetune: `python3 . {backtest,sweep,report}`.
- Ground-truth collector: `python3 . collect`.
- This report: `docs/research/findings/finetune-report.md`.

## 6. Next step (post-collection, mechanical)

The 3-hour collector continues into `data/gt_*/live.duckdb`; when it finishes the same three commands
(`backtest --cache`, `sweep`, `report`) replay the real corpus. That corpus is projected to be thin
(§0: ~150 direct-router victims, ~12–15 sandwiched), so the §2 table will report a small-`n` result and
the §5 go/no-go will remain NO-GO — but now backed by this repo's own measured data rather than the
parent's. The measured public-V2 sandwich rate (0 in 28 blocks) plus the predecessor's negative-EV
study and the unknowable `WIN_RATE` already render the recommendation without waiting:

**Do not go live.** There is no demonstrated positive after-fee edge on the public Uniswap V2 mempool
subset in the current Ethereum landscape.
