"""Minimal async JSON-RPC client for full blocks. Read-only: no signing method is reachable."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp

BLOCK_OPTS = {"encoding": "json", "maxSupportedTransactionVersion": 1,
              "transactionDetails": "full", "rewards": False, "commitment": "confirmed"}
SKIPPED = (-32007, -32009, -32004)   # slot skipped / missing in long-term storage / not available yet


def rpc_url(env: str = "ALCHEMY_RPC_URL") -> str:
    url = os.environ.get(env)
    if not url:
        raise RuntimeError(f"env var {env} is not set")
    return url


class Rpc:
    def __init__(self, url: str, concurrency: int = 16, timeout_s: float = 20.0):
        self.url = url
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.session: aiohttp.ClientSession | None = None
        self.n_calls = self.n_retry = 0

    async def __aenter__(self) -> "Rpc":
        self.session = aiohttp.ClientSession(timeout=self.timeout,
                                             headers={"accept-encoding": "gzip"})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def call(self, method: str, params: list, retries: int = 5) -> Any:
        """Result of one call. A JSON-RPC error with a skipped-slot code returns None."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(retries):
            async with self.sem:
                self.n_calls += 1
                try:
                    async with self.session.post(self.url, json=body) as r:
                        if r.status == 429 or r.status >= 500:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        data = await r.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    self.n_retry += 1
                    await asyncio.sleep(0.5 * 2 ** attempt)
                    continue
            err = data.get("error")
            if err is None:
                return data.get("result")
            if err.get("code") in SKIPPED:
                return None
            self.n_retry += 1
            await asyncio.sleep(0.5 * 2 ** attempt)
        raise RuntimeError(f"{method} failed after {retries} attempts")

    async def slot(self, commitment: str = "confirmed") -> int:
        return await self.call("getSlot", [{"commitment": commitment}])

    async def block(self, slot: int) -> dict | None:
        return await self.call("getBlock", [slot, BLOCK_OPTS])

    async def leaders(self, start: int, limit: int) -> list[str]:
        return await self.call("getSlotLeaders", [start, limit]) or []
