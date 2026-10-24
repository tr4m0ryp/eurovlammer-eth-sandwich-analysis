"""Atomic cross-pool arbitrage scanner (dry-run), serving the live dashboard.

Usage:
    ETH_RPC_URL=https://eth.drpc.org ETH_WS_URL=wss://eth.drpc.org \
      .venv/bin/python -m eth_mev.apps.arb_scanner --port 8765
"""
from __future__ import annotations

import argparse
import asyncio

from ...ingest.rpc import Rpc, rpc_url
from ...serve.server import serve
from .scanner import ArbScreener


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    async with Rpc(rpc_url(), rps=10, concurrency=8) as rpc:
        screener = ArbScreener(rpc)
        runner = await serve(screener.hub, "0.0.0.0", a.port)
        print(f"arb dry-run on http://0.0.0.0:{a.port}")
        try:
            await screener.run(a.hours)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
