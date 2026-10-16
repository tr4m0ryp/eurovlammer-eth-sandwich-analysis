"""Raw ``eth_*`` JSON-RPC client over aiohttp, plus WebSocket subscription helpers.

Mirrors ``tlive/ingest/rpc.py``: a paced HTTP client for one-shot calls (``eth_getBlockByNumber``,
``eth_call``, ``eth_getTransactionReceipt``) and a separate WebSocket subscription surface for the
stream (``newPendingTransactions`` + ``newHeads``). Read-only by default: signing is separate in
``exec/sign.py`` and requires explicit opt-in.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

import aiohttp

# JSON-RPC error codes that mean "not available yet" rather than a real failure.
SKIPPED = (-32000, -32001, -32004, -32601, -32602)


def rpc_url(env: str = "ETH_RPC_URL") -> str:
    url = os.environ.get(env)
    if not url:
        raise RuntimeError(f"env var {env} is not set")
    return url


def ws_url(env: str = "ETH_WS_URL") -> str | None:
    return os.environ.get(env) or None


class Rpc:
    """Paced HTTP client. A 429 costs an exponential backoff; pacing below the limit costs nothing."""

    def __init__(self, url: str, concurrency: int = 8, timeout_s: float = 30.0, rps: float = 12.0):
        self.url = url
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.gap = 1.0 / rps
        self.next_t = 0.0
        self.session: aiohttp.ClientSession | None = None
        self.n_calls = self.n_retry = 0
        self.last_error: Any = None
        self._id = 0

    async def __aenter__(self) -> "Rpc":
        self.session = aiohttp.ClientSession(timeout=self.timeout, headers={"accept-encoding": "gzip"})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def _pace(self) -> None:
        now = asyncio.get_running_loop().time()
        wait = self.next_t - now
        self.next_t = max(now, self.next_t) + self.gap
        if wait > 0:
            await asyncio.sleep(wait)

    async def call(self, method: str, params: list, retries: int = 6) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        for attempt in range(retries):
            async with self.sem:
                await self._pace()
                self.n_calls += 1
                try:
                    async with self.session.post(self.url, json=body) as r:
                        if r.status == 429:
                            self.next_t += 1.0
                        if r.status == 429 or r.status >= 500:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        payload = await r.read()
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.n_retry += 1
                    self.last_error = repr(e)[:200]
                    await asyncio.sleep(min(0.5 * 2 ** attempt, 20))
                    continue
            data = json.loads(payload)
            err = data.get("error")
            if err is None:
                return data.get("result")
            if err.get("code") in SKIPPED:
                return None
            self.n_retry += 1
            self.last_error = err
            await asyncio.sleep(min(0.5 * 2 ** attempt, 20))
        raise RuntimeError(f"{method} failed after {retries} attempts: {self.last_error}")

    # ---- convenience ----
    async def block_by_number(self, number: int | str, full: bool = False) -> Any:
        return await self.call("eth_getBlockByNumber", [hex(number) if isinstance(number, int) else number, full])

    async def transaction_receipt(self, tx_hash: str) -> Any:
        return await self.call("eth_getTransactionReceipt", [tx_hash])

    async def call_view(self, to: str, data: str, block: str = "latest") -> Any:
        return await self.call("eth_call", [{"to": to, "data": data}, block])

    async def chain_id(self) -> Any:
        return await self.call("eth_chainId", [])

    async def gas_price(self) -> Any:
        return await self.call("eth_gasPrice", [])

    async def fee_history(self, blocks: int = 4, newest: str = "latest", pct: list = None) -> Any:
        return await self.call("eth_feeHistory", [blocks, newest, pct or [25, 50, 75]])


class Subscriber:
    """WebSocket subscription: ``eth_subscribe`` to pending txs and/or new heads.

    Delivers the *raw* message payloads strictly in receipt order via an async iterator. A bad
    event never stalls the stream (the ordered-consumer guard from ``tlive/ingest/follow.py``):
    decoding happens downstream and exceptions there are caught per-item.
    """

    def __init__(self, ws_url: str, subscriptions: list[str], message_timeout_s: float = 30.0):
        self.ws_url = ws_url
        self.subscriptions = subscriptions
        self.message_timeout = message_timeout_s
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self.ws_url, heartbeat=30.0, timeout=self.message_timeout)
        for i, sub in enumerate(self.subscriptions):
            await self._ws.send_json({"jsonrpc": "2.0", "id": i + 1, "method": "eth_subscribe", "params": [sub]})

    async def messages(self) -> AsyncIterator[dict]:
        """Yield ``{"subscription": ..., "result": ...}`` dicts in receipt order.

        Reconnects on a dropped socket; a per-message decode error is yielded as
        ``{"error": ..., "raw": ...}`` so one bad event never kills the loop.
        """
        while True:
            try:
                if self._ws is None or self._ws.closed:
                    await self._connect()
                async for msg in self._ws:
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        try:
                            yield json.loads(msg.data)
                        except json.JSONDecodeError as e:
                            yield {"error": repr(e), "raw": msg.data[:200]}
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(1.0)  # back off before reconnect

    async def close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
