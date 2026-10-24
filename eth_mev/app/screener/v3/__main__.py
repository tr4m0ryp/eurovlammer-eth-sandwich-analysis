"""Uniswap V3 sandwich-exposure screener (dry-run), serving the live dashboard.

Usage:
    ETH_WS_URL=wss://eth.drpc.org ETH_RPC_URL=https://eth.drpc.org \
      python3 . v3 --port 8080
"""
from __future__ import annotations

import argparse
import asyncio

from ....onchain.node import Rpc, rpc_url
from ...dashboard.server import serve
from .screener import V3Screener


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    async with Rpc(rpc_url(), rps=12, concurrency=10) as rpc:
        screener = V3Screener(rpc)
        runner = await serve(screener.hub, "127.0.0.1", a.port)
        print(f"V3 screener dry-run serving on http://127.0.0.1:{a.port}")
        try:
            await screener.run(a.hours)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
