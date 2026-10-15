# Helius: account, plan, streaming products, credit burn

Researched 2026-10-15. Every factual claim carries its source. Keys are referenced by prefix
only (`REDACTED_KEY_A-…`, `REDACTED_KEY_B-…`); the full values live in the environment, never here.

## Headline

**The account is at its credit ceiling right now.** Both keys, on both endpoints, answer
HTTP 429 with the body `max usage reached`. That string is Helius's credit-exhaustion form of
429, not a per-second rate limit
([Helius FAQ](https://www.helius.dev/docs/de/faqs.md)). Until credits are added, retrying
cannot help ([Helius billing FAQ](https://www.helius.dev/docs/de/faqs/billing)).

## Account & endpoints

| Endpoint | Key | Observed 2026-10-15 |
|---|---|---|
| `https://mainnet.helius-rpc.com/?api-key=…` | `REDACTED_KEY_A-…` | 3 of 15 burst calls returned 200, the rest 429; afterwards every call `max usage reached` |
| `https://beta.helius-rpc.com/?api-key=…` | `REDACTED_KEY_B-…` | `max usage reached`, HTTP 429 |

Both keys hit the ceiling at the same moment, so they bill against **one account**, not two.

No plan, tier, credit or billing information appears in the response headers: only Cloudflare
headers (`cf-ray`, `cf-cache-status`, `server: cloudflare`), a `zid` header and a `__cf_bm`
cookie. The plan cannot be read by querying the RPC.

**Probe honesty.** Establishing the above took ~38 read-only calls (`getSlot`, `getHealth`), no
transactions. The very first burst already returned 12 refusals out of 15, so the account was
within a few credits of its cap before this research began; I cannot rule out that those first
three successful calls consumed the last of it.

## Plan / tier

**What the evidence supports:** the account is on a plan whose monthly allowance has been spent.
Helius plans and their allowances
([plans](https://www.helius.dev/docs/billing/plans.md)):

| Plan | Price | Monthly credits | RPC rate | LaserStream gRPC mainnet |
|---|---|---|---|---|
| Free | $0 | 1M | 10 req/s | no |
| Developer | $49 | 10M | 50 req/s | devnet only |
| Business | $499 | 100M | 200 req/s | yes |
| Professional | $999 | 200M | 500 req/s | yes |

Autoscaling, which buys credits past the allowance rather than returning 429, is **on by default
only on Business and Professional** ([plans](https://www.helius.dev/docs/billing/plans.md)).
Hitting a hard 429 therefore points at Free or Developer, where autoscaling is not on by
default. Free is the most likely of the two: 1M credits is a week of even light polling.

**Still needs the dashboard.** Open `dashboard.helius.dev`, then:

1. **Plans / Billing** for the tier and price actually being paid.
2. **Credits / Usage** for the month's consumption broken down by method, which is the only way
   to see *what* spent the allowance.
3. **Shreds tab** if a shred seat was ever bought, since seats bill separately per IP
   ([plans](https://www.helius.dev/docs/billing/plans.md)).

I did not attempt a login.

## Gatekeeper (beta)

**It is a network edge, not an MEV or gating product.** Gatekeeper is Helius's own edge gateway,
in public beta, built to take Cloudflare out of the request path; Helius claims tens to hundreds
of milliseconds saved ([Gatekeeper overview](https://www.helius.dev/docs/gatekeeper/overview)).

- **Opt-in and free.** Swap the host, keep the same API key.
- **Carries** standard Solana RPC, Helius RPC methods, WebSockets including
  `transactionSubscribe`, DAS, Photon, Priority Fee and Enhanced Transactions.
- **Does not carry LaserStream**; gRPC still uses the dedicated LaserStream endpoints.
- **Mainnet only** for now, with devnet and testnet to follow.
- The `beta.` in the hostname describes the rollout, not the maturity of the products behind it.
  Rolling back means putting `mainnet.helius-rpc.com` back.

The one product that *is* served from `beta.helius-rpc.com` beyond the gateway is
Preconfirmations, over `wss://beta.helius-rpc.com`
([preconfirmations](https://www.helius.dev/docs/pre-confirmations/overview.md)).

## Geyser gRPC vs Jito mempool

### (a) Geyser gRPC = Helius LaserStream: post-execution, fast

LaserStream is Helius's gRPC streaming product, **wire-compatible with Yellowstone gRPC** and a
fork of Richat, so a Yellowstone client drops in unchanged
([LaserStream](https://www.helius.dev/docs/laserstream)). It streams transactions, slots, blocks
and accounts at `processed`, `confirmed` and `finalized`, filters by account, token account and
mint, and can replay the last 48 hours from a given slot.

- **Availability:** mainnet gRPC requires **Business ($499/mo) or Professional ($999/mo)**;
  devnet is on every plan ([plans](https://www.helius.dev/docs/billing/plans.md)).
- **Billing is by bandwidth, not by call:** **2 credits per 0.1 MB uncompressed**, which is
  **20 credits per MB** ([credits](https://www.helius.dev/docs/billing/credits.md)).
- `processed` is the earliest delivery, and account and program updates come only from this
  post-execution stream, not from shreds ([LaserStream](https://www.helius.dev/docs/laserstream)).

It is faster than polling, and it is still **after execution**. It is not a mempool.

### (b) A true pre-confirmation feed

**Solana has no mempool.** Transactions are forwarded straight to the upcoming leader rather than
sitting in a shared pool ([The Block](https://www.theblock.co/news/ecosystems/2024-03-09-solana-client-jito-labs-axes-mempool-function-following-increase-in-mev-attacks-281504)).

**Jito built one and then killed it.** The Jito Block Engine's mempool is what made Solana
sandwiching practical; Jito axed it on 2024-03-08/09 after weeks of heightened sandwich attacks,
saying the decision followed "deliberate conversations with the Jito Labs team and key Solana
ecosystem stakeholders"
([The Block](https://www.theblock.co/news/ecosystems/2024-03-09-solana-client-jito-labs-axes-mempool-function-following-increase-in-mev-attacks-281504)).
So nobody sells *the Jito mempool*, because it no longer exists.

**The premise "Helius does not sell the mempool" needs correcting.** No vendor sells a pending-tx
pool, because there is none, but Helius does sell the two closest things:

| Product | What it is | Plan | Price |
|---|---|---|---|
| **Shred Delivery** | raw shreds, the fragments a leader distributes before blocks are assembled | any plan | $1,000/mo per IP, $800 on Professional ([plans](https://www.helius.dev/docs/billing/plans.md)) |
| **Preconfirmations** | `preconfSubscribe` over `wss://beta.helius-rpc.com`, transactions as the leader first observes them, "before they are collected into entries and converted into shreds" | **Professional or higher** | 10 credits/msg, min 300 credits per covered slot from 2026-10-14 ([preconfirmations](https://www.helius.dev/docs/pre-confirmations/overview.md)) |

Preconfirmations carry two sources: Helius's own, emitted when the leader executes with a status,
and BAM, emitted when a Jito Block Assembly Marketplace validator commits to execute, before
execution and therefore without status. **Coverage is partial**, limited to validators that
forward to Helius or run a BAM client, with documented gaps
([preconfirmations](https://www.helius.dev/docs/pre-confirmations/overview.md)).

**Jito ShredStream is being retired.** Jito's own docs carry a deprecation notice: shutdown on
**2026-09-05**, with DoubleZero Edge as the recommended replacement
([Jito docs](https://docs.jito.wtf/lowlatencytxnfeed/)). That date has passed, so treat
ShredStream as gone until Jito says otherwise.

Others selling shred-level streams: bloXroute (Shred Stream over its Optimized Feed Relay,
[docs](https://docs.bloxroute.com/solana/optimized-feed-relay/transaction-streamer)), Triton One
(author of the Yellowstone plugin), QuickNode (`shredTransactionSubscribe`, beta) and OrbitFlare.

## Our estimated credit burn

**`getBlock` costs 1 credit**, flat, regardless of payload size; so do `getTransaction`,
`getSignaturesForAddress` and `getSlot`
([credits](https://www.helius.dev/docs/billing/credits.md)).

Polling at 2 calls/sec, following every slot:

| Rate | Calls/day | Credits/day | Credits/30d |
|---|---|---|---|
| 2.0/s (stated) | 172,800 | 172,800 | 5.2M |
| 2.5/s (every slot) | 216,000 | 216,000 | 6.5M |
| 1.75/s (150k/day) | 150,000 | 150,000 | 4.5M |

That overruns a **Free** plan (1M) five-fold and fits inside **Developer** (10M) with room.

**The same blocks over LaserStream gRPC cost far more, not less.** A full block in this repo's
own words is "~3 MB of JSON" (`tlive/ingest/rpc.py`). At 20 credits/MB an unfiltered 3 MB block
is **60 credits against 1 credit for `getBlock`**, a 60x inversion:

| Feed | Per block | Credits/day at 2.5 blocks/s | Credits/30d |
|---|---|---|---|
| `getBlock` polling | 1 credit | 216,000 | 6.5M |
| LaserStream, whole blocks | ~60 credits | ~13.0M | ~389M |
| LaserStream, filtered to pump.fun / PumpSwap (~2% of bytes) | ~1.2 credits | ~259,000 | ~7.8M |

The filtered row is the only one that makes gRPC sane, and it is an estimate: the real number is
whatever share of block bytes the venue programs actually occupy, which should be measured before
committing.

## Recommendation

**Our polling is almost certainly not what drained Helius.** `tlive/ingest/rpc.py:default_pool()`
polls `publicnode.com` plus `ALCHEMY_RPC_URL`; Helius appears only as `HELIUS_RPC_URL`, an
executor fallback in `tlive/run.py:97-100`. The 150-170k `getBlock`/day go to Alchemy and the
public node, not to Helius. Whatever spent the Helius allowance is something else, and the
Credits/Usage page is the only place that says what.

**On moving to the stream:**

1. **Do not stream whole blocks.** It costs roughly 60x what polling costs, because gRPC bills
   bandwidth and `getBlock` bills per call.
2. **A tightly filtered LaserStream subscription is the right shape** for a sandwich screener:
   it removes the 1-slot lag that `start_lag=1` currently accepts, and at ~2% of block bytes it
   lands near the polling cost. It needs **Business, $499/mo**, for mainnet gRPC.
3. **Pre-confirmation is a separate purchase.** Nothing below **Professional, $999/mo**, buys
   Preconfirmations, and even then coverage is partial, so it is a probabilistic edge rather than
   the full pre-trade view a real sandwicher has. Shred Delivery at $1,000/mo per IP is the other
   route to the same earliness.
4. **Cheapest immediate fix**, if Helius is only ever the executor fallback: move to **Developer,
   $49/mo**, which restores headroom without buying a stream we are not yet wired to consume.

**The one thing to confirm in the dashboard:** open `dashboard.helius.dev` → Credits/Usage and
read which method consumed this month's allowance, because that single number decides whether the
fix is a $49 plan bump or a change to what the code calls.
