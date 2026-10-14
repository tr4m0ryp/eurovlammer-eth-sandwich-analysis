"""Minimal async JSON-RPC client for full blocks. Read-only: no signing method is reachable."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp
import orjson

BLOCK_OPTS = {"encoding": "json", "maxSupportedTransactionVersion": 1,
              "transactionDetails": "full", "rewards": False, "commitment": "confirmed"}
SKIPPED = (-32007, -32009, -32004)   # slot skipped / missing in long-term storage / not available yet


def rpc_url(env: str = "ALCHEMY_RPC_URL") -> str:
    url = os.environ.get(env)
    if not url:
        raise RuntimeError(f"env var {env} is not set")
    return url


class Rpc:
    def __init__(self, url: str, concurrency: int = 16, timeout_s: float = 60.0):
        self.url = url
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.session: aiohttp.ClientSession | None = None
        self.n_calls = self.n_retry = 0
        self.last_error: Any = None

    async def __aenter__(self) -> "Rpc":
        self.session = aiohttp.ClientSession(timeout=self.timeout,
                                             headers={"accept-encoding": "gzip"})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def call(self, method: str, params: list, retries: int = 6, raw: bool = False) -> Any:
        """Result of one call. A JSON-RPC error with a skipped-slot code returns None.

        ``raw`` returns the undecoded body for callers that parse off the event loop (a full
        block is ~3 MB of JSON; parsing it here starves every other request into a timeout)."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(retries):
            async with self.sem:
                self.n_calls += 1
                try:
                    async with self.session.post(self.url, json=body) as r:
                        if r.status == 429 or r.status >= 500:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        payload = await r.read()
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.n_retry += 1
                    self.last_error = repr(e)
                    await asyncio.sleep(0.5 * 2 ** attempt)
                    continue
            if raw and payload[:400].find(b'"error"') < 0:
                return payload
            data = orjson.loads(payload)
            err = data.get("error")
            if err is None:
                return data.get("result")
            if err.get("code") in SKIPPED:
                return None
            self.n_retry += 1
            self.last_error = err
            await asyncio.sleep(0.5 * 2 ** attempt)
        raise RuntimeError(f"{method} failed after {retries} attempts: {self.last_error}")

    async def slot(self, commitment: str = "confirmed") -> int:
        return await self.call("getSlot", [{"commitment": commitment}])

    async def block(self, slot: int, raw: bool = False) -> dict | bytes | None:
        return await self.call("getBlock", [slot, BLOCK_OPTS], raw=raw)

    async def leaders(self, start: int, limit: int) -> list[str]:
        return await self.call("getSlotLeaders", [start, limit]) or []
