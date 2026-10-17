# Optimization roadmap (visible + sandwichable / win-rate levers)

Ordered by what moves the needle for the *sandwich-target* engine, outside the tunables already
deployed (finetuned allocation + risk gates). Status reflects the 2026-10-15 codebase.

> ⚠️ **Strategy re-frame (Oct 2026).** The front-run "sandwich-target" strategy this engine models is
> **structurally closed to a retail operator**:
> 1. Solana has **no public mempool** (Gulf Stream forwards txs straight to scheduled leaders), and
>    Jito **shut down its public mempool stream in March 2024** to stop sandwich attacks.
> 2. Jito's block engine runs active **sandwich mitigation** — a tx carrying a `jitodontfront*` account
>    is rejected unless it is first in the bundle (our `dontfront` field). Front-running a protected
>    victim is therefore refused at the block engine, not just hard.
> 3. The remaining sandwich edge sits with **validator/relayer-level privileged access** (being
>    penalised/removed) or extreme low-latency racing — not a retail bot.
>
> Net effect: **WIN_RATE ≈ 0 for us**, so the dry-run's assumed 0.5 is optimistic and "go live" as a
> front-runner has no durable edge. The screener is the correct shape for *analysis + simulation*; a
> real P&L strategy would be a different product (atomic arbitrage / back-running via Jito bundles),
> out of scope here.

| # | Bottleneck | Effect | Fix | Effort | Status / cost |
|---|---|---|---|---|---|
| 1 | No mempool | WIN_RATE — but see re-frame | Geyser gRPC (observe) is the only real upgrade; front-run edge does not exist publicly | high | gRPC paid; Jito send free |
| 2 | Dry-run only | realise the edge | live=True + Jito bundle + settle loop | medium | **no edge without a real strategy — defer** |
| 3 | Region | WIN_RATE (few ms) | EU colo | ops | Amsterdam **and** Frankfurt are both official Jito Block-Engine regions (~5–7 ms apart) |
| 4 | Capital | visible/budget-feasible count | raise cap | — | **deferred — needs funds** |
| 5 | Single-buy vs bursts | sandwichable count | flow-burst aggregation | medium | **not implemented yet** |
| 6 | Optimistic sim | display only | settle vs real sandwiches | medium | already done (wallet vs `victims`) |
| 7 | Race loss | WIN_RATE | fee/tip bidding + fill-rate | medium | not started |
| 8 | Transfer-hook gap | safety FN | validate scanner | low | not started |
| 9 | Python hot path | throughput | Rust rewrite | high | minor |

---

## #4 Capital (deferred until funds are available)

Today the front leg is capped at **0.5 SOL** in two places:

- `tlive/screen/visibility/strict.py` → `BUDGET_LAMPORTS = 500_000_000` (0.5 SOL) — the strict gate
  `0 < front <= budget`.
- `tlive/engine/allocator.py` → `C0 = 0.5` (initial capital + exposure cap) and `budget_sol = 0.5`.

Raising the cap loosens the `front <= budget` gate, which is a hard filter on the strict universe
(measured: strict = 3.76% of buys; commits ≈ 0.008% of buys). More capital ⇒ more budget-feasible
targets ⇒ higher commit & hit count. To quantify the exact upside before funding, re-run
`scripts/finetune/backtest.py` / `strict.py` with `BUDGET_LAMPORTS` at 1 / 2 / 5 SOL and diff the
commit + real-hit counts.

**Revisit once the wallet has accumulated enough SOL.** No code changes are baked in for this yet.

---

## #1 Mempool — what to provide, cost

Current ingest (`tlive/ingest/follow.py`) polls **confirmed** blocks via RPC (`getBlock`, `start_lag=1`),
so we see a buy only after it has already landed. A real sandwicher sees it pre-confirmation.

- **Geyser gRPC stream** (Yellowstone): a validator pushes `transaction`/`block` notices as they are
  processed — lower latency than polling, but still post-confirmation. Providers: Helius (paid),
  QuickNode (paid), Triton (enterprise), Shyft, Blockdaemon.
  - To wire it: provide a **gRPC endpoint URL + auth token** (e.g. Helius `x-token`) and the
    subscription (programs/accounts to watch). Then add an ingest consumer beside `Follower`.
- **True Jito mempool stream** (pre-confirmation pending txs): the actual front-running edge. Sold by
  third-party searcher-infra providers (invite-only / custom). Provide a **WebSocket/gRPC endpoint +
  auth key** that emits pending Jito-bundle and legacy-mempool txs.
- **Send side is free**: `mainnet.block-engine.jito.wtf` is permissionless — no API key needed; bundles
  are authenticated with the (already-generated) auth keypair.

Cost summary: Jito bundle **send = free**; the **mempool/geyser stream = paid** (RPC/webhook free tiers
do not include the geyser stream).

---

## #3 Region — fact check

- Solana has **no single "original server"**: consensus is global and the leader rotates across the
  validator set each slot. There are EU hubs (Frankfurt, Amsterdam, Ireland) but no fixed closest point.
- **Amsterdam vs Frankfurt ≈ 360 km apart ⇒ ~5–7 ms RTT** — below the noise floor of a ~400 ms slot and
  irrelevant for a sandwich bot. Both are equivalent for this workload.
- This box (`REDACTED_IP`, Scaleway **Paris**) already measures ~11 ms connect to
  `api.mainnet-beta.solana.com` and ~20 ms to the Jito block engine.
- **Conclusion:** use the free Oracle tier (Amsterdam) — there is no measurable edge from paying for
  Frankfurt colo. The real latency lever is the ingest type (#1), not the city.