"""WebSocket ``eth_subscribe`` surface for the live stream (pending txs and/or new heads)."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import aiohttp


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
