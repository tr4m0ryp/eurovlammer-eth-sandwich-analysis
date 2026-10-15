# Build the Capital-Allocation Decision Engine + Wallet Tab

## Context

We have a live screener running at `http://REDACTED_IP.nip.io:8765/` that processes Solana blocks in real time. The repo is at `~/projects/trenches-live/` (Python 3.12, DuckDB/Parquet, aiohttp web server).

The screener currently has a **strict visibility module** that filters incoming buys down to only those that are:
1. **Visible** to us (lag < 4 slots, direct submission, no Jito tip)  
2. **Budget-feasible** (front-run ≤ 0.5 SOL, MESP > fees, ROI ≥ 1%)

These filtered rows are published as SSE events (`t: "strict"`) and rendered on the page.

## What needs to be built

### 1. Capital Allocation Decision Engine

A new module `tlive/engine/` that receives each strict event and simulates whether the bot would "commit" to it, given the current wallet state.

The engine must implement the mathematical framework from `notes/capital-allocation-framework.md` (if it exists) OR a practical approximation of it.

**Core logic:**
- Maintain a `wallet` (float, SOL) starting at 0.5 SOL
- For each incoming strict event `e` with `front_budget` (κ) and `mesp_budget` (π):
  - Check if `wallet >= κ(e)` and whether committing is optimal given active positions
  - If committed: deduct κ(e) from wallet, add to `active_positions` with an unlock slot (current slot + random L ∈ [2, 8])
  - When current slot >= unlock slot: add κ(e) + π(e) back to wallet, record the completed trade

**Key behaviors:**
- Multiple positions can be active simultaneously (parallel commitments) — total locked capital ≤ 0.5 SOL
- The wallet grows as profits accumulate (compounding)
- The decision rule should adapt as the wallet grows (percentage-based → absolute sizing)
- Must handle the case where no counterparty arrives (lock expires with loss)

**State to track:**
- `wallet`: current available SOL
- `total_locked`: sum of κ of all active positions
- `positions[]`: each with (pool, κ, π, commit_slot, unlock_slot, status)
- `history[]`: completed trades with (pool, κ, π, realized_gain, commit_slot, unlock_slot)

### 2. New Page Tab: "Wallet"

Add a second tab to the live page alongside the existing "Strict Targets" tab.

**Tab button**: `Wallet`
**Panel contents:**

Stats strip:
- Wallet balance (SOL)
- Total locked (SOL)
- Total profit (SOL, cumulative)
- Number of active positions
- Number of completed sandwiches

Active positions table:
| Time committed | Pool | κ (front) | π (expected) | Unlock in (slots) | Status |

Completed sandwiches table:
| Time committed | Time resolved | Pool | κ (front) | π (realized) | Gain (SOL) | Status (win/loss) |

The wallet tab must update in real-time via the SSE stream, just like the strict targets tab does.

### 3. SSE Topics

Add a new SSE topic `"engine"` (or `"wallet"`) that publishes state updates:
- On commit: `{type: "commit", pool, κ, π, wallet_after, lock_slots}`
- On resolve: `{type: "resolve", pool, κ, π, gain, wallet_after}`
- On stats tick: `{type: "stats", wallet, total_locked, n_active, n_completed, total_profit}`

Update `hub.py` to accept the new topic. Update `server.py` topic list if needed. Update the snapshot to include wallet state.

### 4. Code Integration

In `run.py`:
- Import and instantiate the engine alongside `StrictVisibility`
- After `hub.add_strict(...)`, pass each strict event to the engine
- The engine returns commit decisions and position updates
- Push engine events into the hub via `hub.publish("wallet", ...)`

## Architecture Pattern (follow existing)

The codebase follows these conventions:
- ~300 lines max per file
- 4/4 tree rule (max 4 levels deep)
- Python 3.12, no type annotations in function bodies (PEP 484 in signatures only where helpful)
- Async I/O via aiohttp, blocking work offloaded to ProcessPoolExecutor
- SSE events are serialized with orjson, fanned out by Hub
- New page tabs: add a JS file in `page/live/`, reference it in `page/index.html`
- CSS follows the existing Avelero style in `page/css/`

## Verification

1. Restart the screener
2. Verify the wallet tab shows on the live page
3. Verify wallet starts at 0.5 SOL
4. Wait for strict events to arrive
5. Verify committed positions show in the active table
6. After ~20-60 seconds, verify completed positions appear with gains
7. Verify wallet balance grows from profits

## Files to create/modify

**Create:**
- `tlive/engine/__init__.py` — exports
- `tlive/engine/allocator.py` — the decision engine class
- `tlive/serve/page/live/wallet.js` — wallet tab rendering + SSE handling

**Modify:**
- `tlive/run.py` — integrate engine
- `tlive/serve/hub.py` — add wallet state topic if needed
- `tlive/serve/page/index.html` — add Wallet tab button and panel
- `tlive/serve/artifact.py` — include wallet.js in build if desired