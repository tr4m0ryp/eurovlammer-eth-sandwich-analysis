"""Follow the confirmed tip block by block, decoding each block's swaps off the event loop.

Blocks are requested as soon as the tip passes them and handed to the consumer strictly in slot
order, so every downstream window (pool flow, sandwich legs) sees true chain order. A skipped
slot is delivered as an empty block so the order never stalls on it.
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from typing import AsyncIterator

import orjson

from ..chain.programs import PUMP, PUMPSWAP
from ..chain.swaps import decode_tx
from ..chain.tx import account_keys
from ..metrics.exposure import exposure
from .rpc import RpcPool

MAX_INFLIGHT = 24
MAX_BLOCK_LEAD_S = 120          # a block time this far past the receive clock is not trusted


def decode_block(raw: bytes | None, slot: int) -> dict:
    """Worker: one raw getBlock body -> {slot, block_time, n_tx, swaps: [row dicts]}."""
    if raw is None:
        return {"slot": slot, "block_time": None, "n_tx": 0, "swaps": [], "skipped": True}
    b = orjson.loads(raw).get("result") or {}
    rows = []
    bt = b.get("blockTime")
    for i, tx in enumerate(b.get("transactions") or ()):
        keys = account_keys(tx)
        if PUMP not in keys and PUMPSWAP not in keys:
            continue
        f, sw = decode_tx(tx, slot, i, bt)
        for s in sw:
            r = asdict(s)
            e = exposure(s)                  # screener view: the swap's own pre-state
            r.update(signer=f.signer, router=f.router, cu_price=f.cu_price, priority_fee=f.priority_fee,
                     jito_tip=f.jito_tip, dontfront=f.dontfront, x_ref=s.x_pre, slack=e.slack, mesp=e.mesp, front_opt=e.front)
            rows.append(r)
    return {"slot": slot, "block_time": bt, "n_tx": len(b.get("transactions") or ()), "swaps": rows,
            "skipped": False}


class Follower:
    def __init__(self, pool: RpcPool, workers: int = 3, start_lag: int = 4):
        self.pool = pool
        # forkserver: workers must not inherit the web server's listening socket
        self.exec = ProcessPoolExecutor(workers, mp_context=mp.get_context("forkserver"))
        self.start_lag = start_lag
        self.tip = 0
        self.next_slot = 0
        self.lag_slots = 0
        self.clock_rejects = 0

    async def _fetch(self, slot: int) -> dict:
        raw = await self.pool.block(slot, raw=True)
        for _ in range(3):                   # a fresh tip slot can answer "not available" briefly
            if raw is not None or slot < self.tip - 3:
                break
            await asyncio.sleep(0.5)
            raw = await self.pool.block(slot, raw=True)
        blk = await asyncio.get_running_loop().run_in_executor(self.exec, decode_block, raw, slot)
        bt = blk["block_time"]
        if bt is not None and bt > time.time() + MAX_BLOCK_LEAD_S:
            self.clock_rejects += 1          # forged/corrupt clock: never let it move ours
            blk["block_time"] = None
        return blk

    async def blocks(self) -> AsyncIterator[dict]:
        self.tip = await self.pool.slot()
        self.next_slot = self.tip - self.start_lag
        pending: dict[int, asyncio.Task] = {}
        last_tip_t = 0.0
        while True:
            now = time.monotonic()
            if now - last_tip_t > 0.8:
                try:
                    self.tip = max(self.tip, await self.pool.slot())
                except RuntimeError:
                    pass
                last_tip_t = now
            hi = min(self.tip, self.next_slot + MAX_INFLIGHT)
            for s in range(self.next_slot, hi + 1):
                if s not in pending:
                    pending[s] = asyncio.create_task(self._fetch(s))
            task = pending.get(self.next_slot)
            if task is None:
                await asyncio.sleep(0.2)
                continue
            try:
                blk = await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except RuntimeError:
                blk = {"slot": self.next_slot, "block_time": None, "n_tx": 0, "swaps": [], "skipped": True}
            del pending[self.next_slot]
            self.next_slot += 1
            self.lag_slots = self.tip - blk["slot"]
            yield blk
