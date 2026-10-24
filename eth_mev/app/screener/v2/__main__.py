"""Uniswap V2 sandwich-exposure screener (dry-run by default), served by the dashboard.

Usage:
    ETH_WS_URL=wss://... ETH_RPC_URL=https://... python3 . v2
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from aiohttp import web

from ....onchain.decode.v2.constants import WETH
from ....strategy.execution.bundle.flashbots import live_gates_met
from ....onchain.node.mempool import MempoolIngest
from ....onchain.node import Rpc, rpc_url, ws_url
from ....config import DATA_DIR
from ...dashboard.server import build_app
from .screener import Screener


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=float(os.environ.get("MEV_BUDGET_ETH", "1.0")))
    ap.add_argument("--run-dir", type=str, default=None)
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()

    rpc_url_str = rpc_url()
    ws = ws_url()
    live = os.environ.get("LIVE", "").strip().lower() in ("1", "true", "yes")
    if live:
        ok, missing = live_gates_met()
        if not ok:
            raise RuntimeError(f"LIVE requested but gates unmet: {', '.join(missing)}")

    run_dir = Path(a.run_dir) if a.run_dir else DATA_DIR / time.strftime("live_%Y%m%d_%H%M%S")
    screener = Screener(run_dir, a.budget, live=live)

    app = build_app(screener.hub)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", a.port)
    await site.start()

    async with Rpc(rpc_url_str) as rpc:
        screener.fees = await _fees(rpc)
        ingest = MempoolIngest(rpc, ws, numeraire=WETH)
        ingest.on_pending = lambda row: screener.on_pending(rpc, row)
        ingest.on_head = lambda head: screener.on_head(rpc, head)
        ingest_task = asyncio.create_task(ingest.run())
        print(f"dry-run screener live{' (LIVE MODE)' if live else ''} on http://localhost:{a.port}")
        try:
            await ingest_task
        finally:
            ingest_task.cancel()
            screener.store.close()
            await runner.cleanup()


async def _fees(rpc: Rpc) -> dict:
    try:
        hist = await rpc.fee_history(4, "latest", [50])
        base = int(hist["baseFeePerGas"][-1], 16) if hist else int(20e9)
        return {"base_fee": base, "priority_fee": int(1e9)}
    except Exception:
        return {"base_fee": int(20e9), "priority_fee": int(1e9)}


if __name__ == "__main__":
    asyncio.run(main())
