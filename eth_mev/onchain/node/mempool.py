"""Mempool + block ingestion: pending swaps strictly in receipt order.

Subscribes to ``newPendingTransactions`` (the public mempool) for *pre-inclusion* swaps and to
``newHeads`` for *settlement* (inclusion order). Public geth nodes deliver ``newPendingTransactions``
as *hashes only* (``result`` is a 32-byte hash string), so each hash is resolved to its full tx via
``eth_getTransactionByHash`` before decoding. Pending swaps are handed to the consumer strictly in the
order the hashes arrived; a slow fetch or one bad event never stalls the stream (the ordered-consumer
guard from ``tlive/ingest/follow.py``).
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

from ..decode.v2.constants import ROUTERS as _ROUTERS
from ..decode.v2.constants import WETH
from ..decode.v2.swaps import decode_v2_router
from ..decode.tx import normalized_tx
from . import Rpc, Subscriber


class MempoolIngest:
    """Wires WS subscriptions to decoded, strictly-ordered delivery.

    ``on_pending`` (optional) receives decoded pending-swap row dicts; ``on_block`` (optional)
    receives a confirmed block's header+tx list for settlement. Callbacks run on the ingest loop.
    """

    def __init__(self, rpc: Rpc, ws_url: str, numeraire: str = WETH):
        self.rpc = rpc
        self.ws_url = ws_url
        self.numeraire = numeraire
        self.on_pending: Callable[[dict], Any] | None = None
        self.on_block: Callable[[dict], Any] | None = None
        self.on_head: Callable[[dict], Any] | None = None

    async def _full_tx(self, tx_hash: str) -> dict | None:
        """Resolve a pending hash to its full tx (defensive: never assume it still exists)."""
        try:
            tx = await self.rpc.call("eth_getTransactionByHash", [tx_hash])
        except RuntimeError:
            return None
        return tx if isinstance(tx, dict) else None

    async def _decode_pending(self, tx_hash: str) -> list[dict]:
        tx = await self._full_tx(tx_hash)
        if not tx or not tx.get("input"):
            return []
        n = normalized_tx(tx)
        desc = decode_v2_router({"to": tx.get("to"), "input": tx.get("input"),
                                 "hash": tx_hash, "from": n["from"], "transactionIndex": 0}, _ROUTERS)
        if not desc:
            return []
        rows = []
        for d in desc:
            rows.append({**d, "hash": tx_hash, "from": n["from"], "value": n["value"],
                         "gas": n["gas"], "maxPriorityFeePerGas": tx.get("maxPriorityFeePerGas"),
                         "maxFeePerGas": tx.get("maxFeePerGas"), "nonce": n["nonce"], "type": "pending"})
        return rows

    async def _handle_head(self, payload: dict) -> None:
        """A newHeads header confirms the previous block; hand it to the settlement callback."""
        if self.on_head:
            self.on_head(payload)
        if self.on_block:
            self.on_block(payload)

    async def run(self) -> None:
        """Drive both subscriptions until cancelled. Routes by payload shape, not sub id:
        a pending tx is a hex hash string; a newHeads header is a dict carrying ``number``."""
        sub = Subscriber(self.ws_url, ["newPendingTransactions", "newHeads"])
        try:
            async for msg in sub.messages():
                if "error" in msg:
                    continue
                # Subscription confirmation carries no ``params``; skip it.
                if "params" not in msg:
                    continue
                params = msg.get("params")
                if not isinstance(params, dict):
                    continue
                payload = params.get("result")
                # newHeads: dict with a block number.
                if isinstance(payload, dict) and "number" in payload:
                    await self._handle_head(payload)
                # newPendingTransactions (hash-only on public geth): hex string.
                elif isinstance(payload, str) and payload.startswith("0x") and len(payload) == 66:
                    for row in await self._decode_pending(payload):
                        if self.on_pending:
                            self.on_pending(row)
        finally:
            await sub.close()

    async def events(self) -> AsyncIterator[dict]:
        """Async iterator form: yields ``{"kind": "pending"|"head", ...}`` events in receipt order."""
        q: asyncio.Queue = asyncio.Queue()

        async def _pending(row):
            await q.put({"kind": "pending", **row})

        async def _head(payload):
            await q.put({"kind": "head", **payload})

        self.on_pending = _pending
        self.on_head = _head
        task = asyncio.create_task(self.run())
        try:
            while True:
                ev = await q.get()
                yield ev
        finally:
            task.cancel()
