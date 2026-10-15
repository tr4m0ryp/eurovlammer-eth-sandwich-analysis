# Execution plan: turn the screener into a live sandwich trader

Status: build-in-progress. **No live deployment this session** (per user). Goal is the real
execution path (sign → submit → bundle), replacing the simulated wallet. The user funds it once built.

## 1. Real vs simulated today
- **Real / already production:** block follower (Alchemy), swap decoders (pump.fun + PumpSwap),
  scorer, strict visibility (golden-section exposure on on-chain state), Jito bundle *observation*
  (labeling only).
- **Simulated (to be replaced):** the `tlive/engine/allocator.py` wallet — it never submits a tx.
  The `wallet` tab is a what-if sandbox (Bernoulli win/loss, full π on win).

## 2. Credentials / secrets needed (answer to "any other API keys?")
**No additional *API keys* are required** — the RPCs are already present. What's missing are two
ed25519 **keypairs** (secrets, not API keys), plus optional infra:

| Item | Have? | Notes |
|---|---|---|
| `ALCHEMY_RPC_URL` | ✅ | block following (read path) |
| `HELIUS_RPC_URL` | ✅ | can double as the `sendTransaction` path |
| Birdeye / GMGN / X | ✅ | token metadata + research, unused by execution |
| **Funded trader keypair** | ❌ needed | ed25519 JSON keypair the bot signs front/back legs with; user funds with SOL. Env `TLIVE_TRADER_KEYPAIR` |
| **Jito auth keypair** | ❌ needed | second ed25519 keypair, *unfunded*, for bundle auth + tip account. I can generate this. Env `TLIVE_JITO_KEYPAIR` |
| Low-latency send RPC | optional | Alchemy/Helius may rate-limit under bot load; a dedicated send node helps |
| Yellowstone gRPC (Geyser) | optional | mempool-level visibility if we want same-block front-running (current model is cross-slot, lag<4) |

Jito Block Engine (`mainnet.block-engine.jito.wtf`) is permissionless — no API key; you authenticate
bundles with the auth keypair.

## 3. Toolchain
- Installed into `.venv`: `solders` (keypair/message/tx/signing) and `anchorpy` 0.21.
- Verified solders end-to-end: keypair gen → instruction → message → sign → base58 serialize.
- Repo has no `requirements.txt`; venv is seeded ad-hoc (`pip install solders anchorpy`).

## 4. Reconstructed instruction account layouts (real mainnet, 2026-10-15)
Extracted from live blocks with signer/writable flags decoded from the message header. This is the
exact account order the programs expect today.

### pump.fun `buy` (18 accounts; args = amount_out, max_sol_cost)
```
 idx  flag      role (best effort)
 0    ro        global?
 1    w         fee_recipient?
 2    ro        mint
 3    w         bonding_curve
 4    w         associated_bonding_curve
 5    w         associated_user (buyer token ATA)
 6    S w       user (payer)
 7    ro        system program (1111...)
 8    ro        token program (TokenzQdBN... or Tokenkeg...)
 9    w         ?  (newer)
 10   ro        event authority (Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1)
 11   ro        program (6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P)
 12   w         ?  (newer)
 13   w         ?  (newer)
 14   ro        ?  (newer)
 15   ro        pfee (pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ)
 16   ro        ?  (newer)
 17   w         ?  (newer)
```
pump.fun `sell` (16 accounts) mirrors it minus bonding-curve-mint extras; args = amount_in, min_sol_out.

### PumpSwap `buy` (26 accounts; args = base_out, max_quote_in) / `sell` (24; args = base_in, min_quote_out)
```
 0  w  pool (authority)
 1  S w  user
 2  ro  pool state (ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw = pump authority?)
 3  ro  base mint (…pump)
 4  ro  quote mint (So1111… = WSOL)
 5-10    ATAs / vaults (w) + config (ro)
 11 ro  token program (Tokenkeg)
 12 ro  token program (dup)
 13 ro  system program
 14 ro  associated-token program (ATokenGPvb…)
 15 ro  global/config
 16 ro  program (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA)
 17+     fee recipients (pfee), coin-creator fee, misc (mixed w/ro)
```
Note: exact *role* assignment of the newer pump.fun + PumpSwap accounts is the remaining unknown;
the order + flags above are exact and were taken from successful mainnet txs.

## 5. Architecture (new `tlive/exec/`, dry-run by default)
- `keypair.py` — load trader + jito keypairs from env paths (never log secrets).
- `accounts.py` — constant program/account addresses + the reconstructed templates above.
- `build.py` — build the front-run `buy` and back-run `sell` instructions (args + account metas),
  substituting the wallet's ATA / bonding curve.
- `submit.py` — sign + send: Jito bundle (front, back, tip) and/or plain RPC `sendTransaction`.
- `executor.py` — orchestrates a strict event → build → simulate → bundle → confirm → unwind,
  with hard safety gates (slippage cap, max loss, exposure ≤ budget). **Default = simulate only.**

## 6. Next steps
1. Finalize account-role mapping (cross-reference more txs + the public IDL JSON mirror).
2. Implement `build.py` (front buy + back sell) for both venues.
3. Validate with `simulateTransaction` (no funds needed) before any live send.
4. Jito bundle submission path.
5. Safety: slippage limits, profit ≥ fee+tip hurdle, single-flight per pool, kill switch.

## 7. Progress log (rounds 3+)
- **Decision: never reconstruct the layout from an IDL.** The 2026 pump.fun program has grown
  per-token accounts (pfee program, sell-vs-buy-specific vaults) that are **not derivable from the
  mint** with simple `[seed, mint]` PDAs (brute-forced ~28 seeds against PUMP and the `pfee` program
  — none matched). The robust, version-proof rule is to **reuse a real transaction's account set and
  swap only the signer + its ATA**.
- **Front-run (buy):** copy the victim's own buy instruction (accounts + discriminator), swap
  `user`→trader and `associated_user`→trader's ATA. No derivation of the mystery accounts needed.
- **Back-run (sell):** `fetch_sell_template()` pulls a recent `sell`/`sell_v2` instruction for the
  mint via `getSignaturesForAddress` + `getTransaction`, then repoints seller + ATA. Cached per mint.
- **Implemented in `tlive/exec/`:** `keypair.py` (load 32/64-byte JSON keypairs), `accounts.py`
  (constants + `bonding_curve` PDA + `associated_token_address`), `build.py` (`repoint_to_wallet`,
  `find_token_program`), `template.py` (`fetch_sell_template`). All derivation vectors verified
  against real mainnet addresses; `tests/unit/test_exec.py` green (20 tests total in the suite).
- **Removed `anchorpy`** (its pytest plugin required `pytest-asyncio` and broke the suite); `solders`
  is the only Solana SDK needed.
- **Next:** assemble the signed `VersionedTransaction` (recent blockhash + front/back instructions +
  args), Jito bundle submit, safety gates (freeze-authority / sell-tax checks), and the
  `simulateTransaction` validation harness.

## 8. Progress log (rounds 9+)
- Added `extract.py` (`buy_from_tx`, `mint_from_tx`, `account_flags`): pulls the victim's buy
  (discriminator + accounts + signer/writable flags, with loaded-address handling) and the mint from
  the TradeEvent — the inputs the front-run needs. `submit.py` now assembles + signs a legacy
  `VersionedTransaction` (`Message.new_with_blockhash`) and simulates.
- **RPC gotcha found and fixed:** this RPC expects the wire tx **base64**-encoded; base58 makes it
  mis-decode and report a bogus `"invalid transaction discriminator"`.
- **`repoint_to_wallet` made robust** to WSOL-quoted buys that list both Tokenkeg and Token-2022: it
  now tries both token programs and picks the one whose ATA actually appears in the account list.
- **End-to-end validated** against live mainnet: extract victim buy → repoint to a throwaway signer
  → assemble + sign → `simulateTransaction` returns `AccountNotFound` (the expected result for an
  unfunded throwaway). The transaction deserialises and account-loads correctly; the only missing
  input is real funds. Suite: 20 passed.
- **Remaining:** front/back size args (from `κ`/`π` + the curve math), Jito bundle submit, safety
  gates (freeze-authority + sell-tax), and wiring into `run.py` (dry-run first).

## 9. Progress log (round 15)
- `sandwich.py`: instruction-data builders — `front_buy_data(x,y,κ,fee)` (pump `buy` exact-out:
  tokens_out = `tokens_for_net`, max_sol = `κ(1+r)`), `sell_data(tokens_in, min_sol_out)`, and
  `break_even_sell_floor(κ,fee,loss_frac)` for the slippage floor. Args re-derived from the curve,
  not copied.
- `safety.py`: the rug guardrail — reads the mint account's **freeze_authority** flag (offset 46 in
  the SPL/Token-2022 Mint layout); `check_token()` fails closed on unknown accounts. Verified on real
  pump mints (freeze=False → safe) and a bad address (→ not safe).
- **Front leg with computed args validated** end-to-end on live mainnet: `decode_tx` → reserves →
  `front_buy_data` → repoint → assemble → sign → simulate returns `AccountNotFound` (unfunded
  throwaway, expected). Suite: 20 passed; `tlive.exec` exports 11 names.
- **Remaining:** full two-leg assembly (front buy + back sell in one bundle), Jito bundle submit,
  and wiring into `run.py` (dry-run).

## 10. Progress log (round 16)
- `bundle.py`: Jito Block Engine client — `get_tip_accounts()` (JSON-RPC `getTipAccounts`, returns the
  same 8 accounts as `chain/programs.py`), `build_tip_tx()` (signed SOL transfer to a tip account),
  `send_bundle()` (`sendBundle`, base64 legs). Verified live: 8 tip accounts, signed tip tx, and a
  `sendBundle` round-trip (rejected for a fake blockhash, i.e. the client is correct end-to-end).
- `template.py` now returns the sell instruction's **flags** too (`account_flags` from `extract.py`),
  so the back-run rebuilds exact account metas.
- `executor.py` — `SandwichExecutor`: safety-check → fetch victim → extract buy → front-run →
  (later) sell template → back-run, with the sell template cached per mint. **Dry-run by default**
  (`live=False`); submission gated on a funded keypair.
- **Full orchestrator validated end-to-end** on live mainnet: `front_run` (safety ok → κ=0.70 SOL,
  18T tokens → simulate `AccountNotFound`) and `back_run` (sell template → floor 0.70 SOL → simulate
  `AccountNotFound`). Both legs build, sign and deserialise correctly; only real funds are missing.
  Suite: 20 passed; `tlive.exec` exports 15 names.
- **Remaining:** wire `SandwichExecutor` into `run.py` (dry-run per strict event), add the sell-tax /
  transfer-hook check to `safety.py`, then flip `live=True` once a funded keypair exists.

## 11. Progress log (round 17)
- **Wired `SandwichExecutor` into `run.py`** — the screener now dry-runs the real executor on every
  strict event, replacing the *simulated* decision with the *actual* front-run build + simulate:
  - loads the trader keypair from `TLIVE_TRADER_KEYPAIR` when set, else a throwaway (dry-run),
  - opens a dedicated `aiohttp` session, and for each strict row calls `front_run` (safety → fetch
    victim → extract buy → build front → simulate),
  - counts commits/rejects/errors in `Stats` (surfaced in `st.view()`/`exec_commits` etc.) and logs
    each decision to the console.
- `py_compile` + import clean; suite still 20 passed. The executor path is now *in* the screener, not
  a side script. (Sequential `await` per event is fine at ~2 strict events/min; offloading to a task
  is a later optimization.)
- **Remaining:** sell-tax / transfer-hook check in `safety.py`, then `live=True` + Jito bundle on a
  funded keypair.

## 12. Risk recognition (rounds 18+)
- `safety.py` extended: `mint_authority` (offset 0 — dev can mint/dump), `freeze_authority` (offset 46),
  and `risky_extensions` (token-2022 TransferHook / NonTransferable / TransferFee via a TLV scan with
  exact payload sizes + a structural guard against metadata-string false positives).
- `risk.py` (new): `event_risks` (slippage = κ/pool-depth, counterparty = model score p, race = the
  victim's priority fee) and `gate` (hard-rejects token risks; soft-rejects past thresholds).
- Wired into `run.py` **always on**: every strict event is safety+risk checked (1 `getAccountInfo` per
  event) before the (opt-in) executor. Counts surface as `risk_safe` + `risk_rejects` (clean category
  names) in the stats.
- Verified live: the first scan false-positived `non_transferable` (matching a 9-byte string length);
  fixed with exact-size + next-extension-type validation. Live breakdown is now
  `high_priority_fee` / `slippage` / `low_confidence` only. Suite: 23 passed.
