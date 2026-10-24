"""The Uniswap V3 sandwich screener (dry-run): per-block decode, pool-state batch fetch, scoring.

Watches confirmed blocks (``newHeads``), decodes V3 ``Swap`` events from receipts, resolves each
pool's state (slot0/liquidity/fee/ticks), computes the concentrated-liquidity sandwich exposure, and
pushes "strict" targets (MESP > round-trip fee) to the dashboard. Measurement only: nothing is sent.
"""
from __future__ import annotations

import os

from ...chain.tx import normalized_tx
from ...chain.v3.pool import fetch_pool_states_bulk
from ...chain.v3.swaps import decode_v3_swap_logs
from ...engine.allocation.v3 import V3Allocator
from ...engine.guard_estimator import GuardEstimator
from ...ingest.rpc import Rpc, new_heads
from ...paths import DATA_DIR
from ...serve.hub import Hub
from .decode import decode_victim
from .scoring import ScoringMixin


class V3Screener(ScoringMixin):
    def __init__(self, rpc: Rpc):
        self.rpc = rpc
        self.hub = Hub()
        self.alloc = V3Allocator()
        self.default_slippage = float(os.environ.get("MEV_DEFAULT_SLIPPAGE", "0.01"))  # fallback only
        self.guard_est = GuardEstimator(
            fallback=self.default_slippage,
            percentile=float(os.environ.get("MEV_GUARD_PERCENTILE", "50")),
            db_path=os.environ.get("MEV_GUARD_DB", str(DATA_DIR / "guard_estimator.duckdb")),
        )
        self.n_blocks = self.n_swaps = self.n_strict = self.n_sandwiches = 0
        self.pool_cache: dict[str, dict] = {}
        self.pool_freq: dict[str, int] = {}
        # Funnel counters for the strict-rate analysis (why are targets so rare?).
        self.funnel = {
            "decoded": 0,            # Swap events decoded from receipts
            "with_pool_state": 0,    # had resolvable pool liquidity
            "router_decoded": 0,     # direct Uniswap router call decoded a guard
            "aggregator_decoded": 0, # aggregator (1inch/0x/paraswap) decoded a guard
            "default_decoded": 0,    # reconstructed from the Swap event + assumed slippage
            "guardless": 0,          # guard decoded but amountOutMinimum == 0
            "mesp_zero": 0,          # guard present but exposure mesp <= 0 (<= round-trip fee)
            "mesp_positive": 0,      # strict (what we surface)
        }

    async def settle_block(self, number: int) -> None:
        try:
            blk = await self.rpc.block_by_number(number, full=True)
        except RuntimeError:
            return
        if not isinstance(blk, dict) or not blk.get("transactions"):
            return
        txs = blk.get("transactions") or []
        # One RPC call for every receipt in the block (chain speed).
        try:
            receipts = await self.rpc.block_receipts(number) or []
        except RuntimeError:
            receipts = []
        receipts_by_hash = {}
        for i, r in enumerate(receipts):
            if isinstance(r, dict) and r.get("transactionHash"):
                receipts_by_hash[r["transactionHash"].lower()] = r

        # Pass 1: decode every V3 swap, remember its (pool, origin). No RPC work here.
        decoded: list[tuple] = []       # (swap, origin, number)
        for txi, tx in enumerate(txs):
            if not isinstance(tx, dict):
                continue
            n = normalized_tx(tx)
            if not n["to"] or not n["input"] or n["input"] == "0x":
                continue
            calldata, via_aggregator = decode_victim(n, tx.get("input"))
            receipt = receipts_by_hash.get(n["hash"].lower())
            logs = (receipt or {}).get("logs") or []
            for s in decode_v3_swap_logs(n["hash"], number, txi, logs, calldata):
                if via_aggregator:
                    s.raw["via_aggregator"] = True
                decoded.append((s, n["from"], number))

        # Pass 2: batch-fetch pool state for every unique pool in ONE round-trip.
        pools = list({s.pool for s, _, _ in decoded} - set(self.pool_cache))
        if pools:
            fresh = await fetch_pool_states_bulk(self.rpc, pools)
            self.pool_cache.update(fresh)

        # Pass 3: score (pure CPU, no RPC).
        strict_rows: list[dict] = []
        for s, origin, blkno in decoded:
            self.n_swaps += 1
            state = self.pool_cache.get(s.pool)
            if not state or not state.get("liquidity"):
                continue
            row = self._score(s, state, origin, blkno)
            if row:
                strict_rows.append(row)
        self.n_blocks += 1
        if strict_rows:
            self.hub.add_strict(strict_rows)
            self.n_strict += len(strict_rows)
            # Feed the capital allocator: resolve matured positions, commit the best targets.
            self.alloc.step(number, strict_rows)
        else:
            # still resolve matured positions on empty blocks
            self.alloc.step(number, [])
        self.hub.set_wallet(self.alloc.state())
        self._publish_stats()

    def _publish_stats(self) -> None:
        self.hub.set_stats({
            "blocks": self.n_blocks, "swaps": self.n_swaps, "strict": self.n_strict,
            "sandwiches": self.n_sandwiches, "block": self.n_blocks,
            "funnel": dict(self.funnel),
            "wallet": self.alloc.stats(),
            "guard": self.guard_est.stats(),
        })

    async def run(self, hours: float) -> None:
        try:
            async for head in new_heads(hours):
                await self.settle_block(head["number"] - 1)
        finally:
            self.guard_est.close()
