"""Minimal async JSON-RPC client for full blocks. Read-only: no signing method is reachable."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp
import orjson

BLOCK_OPTS = {"encoding": "json", "maxSupportedTransactionVersion": 1,
              "transactionDetails": "full", "rewards": False, "commitment": "confirmed"}
SKIPPED = (-32007, -32009, -32004, -32001)   # skipped / not in storage / not yet available / pruned


def rpc_url(env: str = "ALCHEMY_RPC_URL") -> str:
    url = os.environ.get(env)
    if not url:
        raise RuntimeError(f"env var {env} is not set")
    return url


class Rpc:
    """Paced client. A 429 costs an exponential backoff; pacing below the limit costs nothing."""

    def __init__(self, url: str, concurrency: int = 16, timeout_s: float = 60.0, rps: float = 18.0):
        self.url = url
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.gap = 1.0 / rps
        self.next_t = 0.0
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

    async def _pace(self) -> None:
        now = asyncio.get_running_loop().time()
        wait = self.next_t - now
        self.next_t = max(now, self.next_t) + self.gap
        if wait > 0:
            await asyncio.sleep(wait)

    async def call(self, method: str, params: list, retries: int = 8, raw: bool = False) -> Any:
        """Result of one call. A JSON-RPC error with a skipped-slot code returns None.

        ``raw`` returns the undecoded body for callers that parse off the event loop (a full
        block is ~3 MB of JSON; parsing it on the loop starves every other request)."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
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
            if raw and payload.find(b'"error"', 0, 400) < 0:
                return payload
            data = orjson.loads(payload)
            err = data.get("error")
            if err is None:
                return data.get("result")
            if err.get("code") in SKIPPED:
                return None
            self.n_retry += 1
            self.last_error = err
            await asyncio.sleep(min(0.5 * 2 ** attempt, 20))
        raise RuntimeError(f"{method} failed after {retries} attempts: {self.last_error}")

    async def slot(self, commitment: str = "confirmed") -> int:
        return await self.call("getSlot", [{"commitment": commitment}])

    async def block(self, slot: int, raw: bool = False) -> dict | bytes | None:
        return await self.call("getBlock", [slot, BLOCK_OPTS], raw=raw)

    async def leaders(self, start: int, limit: int) -> list[str]:
        return await self.call("getSlotLeaders", [start, limit]) or []


class RpcPool:
    """Several paced endpoints behind one ``block``/``leaders``/``slot`` surface.

    Each request goes to the endpoint whose pacing clock frees up first, so the pool runs at
    roughly the sum of the endpoints' rates; a failing endpoint falls through to the next."""

    def __init__(self, specs: list[tuple[str, float]], concurrency: int = 8):
        self.rpcs = [Rpc(url, concurrency=concurrency, rps=rps) for url, rps in specs]
        self.vclock = [0.0] * len(self.rpcs)    # weighted round-robin: share follows rps

    async def __aenter__(self) -> "RpcPool":
        for r in self.rpcs:
            await r.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        for r in self.rpcs:
            await r.__aexit__(*exc)

    @property
    def n_calls(self) -> int:
        return sum(r.n_calls for r in self.rpcs)

    @property
    def n_retry(self) -> int:
        return sum(r.n_retry for r in self.rpcs)

    async def _try(self, fn: str, *args, **kw):
        i = min(range(len(self.rpcs)), key=self.vclock.__getitem__)
        self.vclock[i] += self.rpcs[i].gap
        order = [self.rpcs[i]] + [r for j, r in enumerate(self.rpcs) if j != i]
        err: Exception | None = None
        for r in order:
            try:
                return await getattr(r, fn)(*args, **kw)
            except RuntimeError as e:
                err = e
        raise err

    async def block(self, slot: int, raw: bool = False):
        return await self._try("block", slot, raw=raw)

    async def leaders(self, start: int, limit: int) -> list[str]:
        return await self._try("leaders", start, limit)

    async def slot(self, commitment: str = "confirmed") -> int:
        return await self._try("slot", commitment)


def default_pool() -> RpcPool:
    """Alchemy (from the env) plus the public endpoint that serves full blocks.

    ``TL_RPC_SPECS="url=rps,url=rps"`` replaces the set (the AWS pull workers use publicnode only:
    its limit is per IP, Alchemy's is per key)."""
    env = os.environ.get("TL_RPC_SPECS")
    if env:
        return RpcPool([(u, float(r)) for u, r in (x.rsplit("=", 1) for x in env.split(","))])
    specs = [("https://solana-rpc.publicnode.com", 7.0)]   # api.mainnet-beta 429s every getBlock
    if os.environ.get("ALCHEMY_RPC_URL"):
        specs.insert(0, (os.environ["ALCHEMY_RPC_URL"], 7.0))
    return RpcPool(specs)
