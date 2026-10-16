"""run_v3.py — the Uniswap V3 sandwich screener (dry-run), serving the live dashboard.

Watches confirmed blocks (``newHeads``), decodes V3 ``Swap`` events from receipts, resolves each
pool's state (slot0/liquidity/fee/ticks), computes the concentrated-liquidity sandwich exposure, and
pushes "strict" targets (MESP > round-trip fee) to the dashboard. **Dry-run only** — no funded key,
nothing sent; this is the live visibility layer the objective asks to host.

Usage:
    ETH_WS_URL=wss://eth.drpc.org ETH_RPC_URL=https://eth.drpc.org \
      .venv/bin/python run_v3.py --port 8080
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from eth_mev.chain.tx import normalized_tx
from eth_mev.chain.v3_pool import fetch_pool_states_bulk
from eth_mev.chain.v3_swap import decode_v3_swap_logs, decode_v3_router
from eth_mev.chain.v3_constants import V3_ROUTERS
from eth_mev.metrics.v3_exposure import V3Pool, exposure
from eth_mev.metrics.v3_math import tick_at_sqrt_ratio
from eth_mev.ingest.rpc import Rpc, rpc_url, ws_url
from eth_mev.serve.hub import Hub
from eth_mev.serve.server import serve

CAPITAL_WEI = int(1e18)  # 1 ETH front budget (dry-run)


class V3Screener:
    def __init__(self, rpc: Rpc):
        self.rpc = rpc
        self.hub = Hub()
        self.n_blocks = self.n_swaps = self.n_strict = self.n_sandwiches = 0
        self.pool_cache: dict[str, dict] = {}
        self.pool_freq: dict[str, int] = {}

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
            calldata = decode_v3_router({"to": n["to"], "input": tx.get("input"),
                                         "hash": n["hash"], "from": n["from"]})
            receipt = receipts_by_hash.get(n["hash"].lower())
            logs = (receipt or {}).get("logs") or []
            for s in decode_v3_swap_logs(n["hash"], number, txi, logs, calldata):
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
        self._publish_stats()

    async def _pool_state(self, pool: str) -> dict:
        return self.pool_cache.get(pool) or {}

    def _score(self, s, state: dict, origin: str, number: int) -> dict | None:
        # Build the V3Pool from on-chain state; tick bitmap is fetched lazily (empty here = single
        # active range; fine for the current-tick exposure).
        if not state.get("sqrt_price_x96"):
            return None
        pool = V3Pool(
            sqrt_price_x96=state["sqrt_price_x96"], liquidity=state["liquidity"],
            tick=state["tick"], tick_spacing=state["tick_spacing"], fee_ppm=state["fee_ppm"],
            ticks={}, token0=state.get("token0", ""), token1=state.get("token1", ""),
            numeraire_is_token0=True,
        )
        # The victim's amount/limit come from the router calldata; a guard must be present to compute
        # exposure. When the tx is not a direct V3 router call (aggregator), we have no guard -> skip.
        if s.amount_in is None or s.amount_out_min is None:
            return None
        victim = {"zero_for_one": s.direction > 0, "amount_in": s.amount_in, "limit": s.amount_out_min}
        e = exposure(pool, victim, capital=CAPITAL_WEI)
        if e.mesp <= 0 or e.front <= 0:
            return None
        self.pool_freq[s.pool] = self.pool_freq.get(s.pool, 0) + 1
        return {
            "tx_hash": s.tx_hash, "block": number, "pool": s.pool,
            "fee_ppm": s.fee, "price": pool.spot_price, "amount_in": s.amount_in,
            "amount_out_min": s.amount_out_min, "mesp": e.mesp, "front": e.front,
            "roi": e.mesp / e.front if e.front else 0.0, "slack": e.slack,
            "pool_freq": self.pool_freq[s.pool], "zero_for_one": s.direction > 0,
            "seen_ms": int(time.time() * 1000),
        }

    def _publish_stats(self) -> None:
        self.hub.set_stats({
            "blocks": self.n_blocks, "swaps": self.n_swaps, "strict": self.n_strict,
            "sandwiches": self.n_sandwiches, "block": self.n_blocks,
        })

    async def run(self, hours: float) -> None:
        deadline = time.monotonic() + hours * 3600
        from eth_mev.ingest.rpc import Subscriber
        sub = Subscriber(ws_url() or rpc_url().replace("https://", "wss://"), ["newHeads"])
        try:
            async for msg in sub.messages():
                if time.monotonic() > deadline:
                    break
                if "error" in msg or "params" not in msg:
                    continue
                params = msg.get("params")
                if not isinstance(params, dict):
                    continue
                payload = params.get("result")
                if not (isinstance(payload, dict) and "number" in payload):
                    continue
                try:
                    number = int(payload["number"], 16)
                except (TypeError, ValueError):
                    continue
                await self.settle_block(number - 1)
        finally:
            await sub.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    async with Rpc(rpc_url(), rps=12, concurrency=10) as rpc:
        screener = V3Screener(rpc)
        runner = await serve(screener.hub, "0.0.0.0", a.port)
        print(f"V3 screener dry-run serving on http://0.0.0.0:{a.port}")
        try:
            await screener.run(a.hours)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
