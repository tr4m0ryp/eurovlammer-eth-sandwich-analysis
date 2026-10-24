"""run.py — the dry-run screener wiring: ingest -> decode -> exposure -> strict -> risk -> allocator ->
settle, served by the dashboard.

Default mode is **dry-run**: it watches the mempool, scores, screens, and (virtually) commits capital,
settling against real on-chain sandwich ground truth — but it sends nothing. The live execution path
(``exec/``) is fully built yet disabled until the operator provides funded keys and, explicitly,
flips ``LIVE=1``.

Usage:
    ETH_WS_URL=wss://... ETH_RPC_URL=https://... .venv/bin/python run.py
    LIVE=1 ETH_BOT_KEY=0x... .venv/bin/python run.py   # still requires funded key + sign-off
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from eth_mev.chain.constants import WETH
from eth_mev.chain.tx import normalized_tx
from eth_mev.engine.allocator import Allocator
from eth_mev.exec.flashbots import FlashbotsRelay, live_gates_met
from eth_mev.exec.risk import event_risks, gate
from eth_mev.ingest.mempool import MempoolIngest
from eth_mev.ingest.rpc import Rpc, rpc_url, ws_url
from eth_mev.screen.labels import Labeller
from eth_mev.screen.scorer import Scorer
from eth_mev.screen.strict import StrictScreener
from eth_mev.serve.server import Hub, build_app
from eth_mev.store.writer import Store

DATA_DIR = Path(__file__).resolve().parent / "data"


def _resolve_fee_heads() -> dict:
    """EIP-1559 fee fields for the current block (base_fee read once; priority fee defaults)."""
    return {"base_fee": int(os.environ.get("MEV_BASE_FEE_WEI", str(20_000_000_000))),
            "priority_fee": int(os.environ.get("MEV_PRIORITY_FEE_WEI", str(1_000_000_000)))}


class Screener:
    def __init__(self, run_dir: Path, budget_eth: float, live: bool = False):
        self.run_dir = run_dir
        self.meta = {"started_ms": int(time.time() * 1000), "live": live, "budget_eth": budget_eth}
        self.store = Store(run_dir, self.meta)
        self.scorer = Scorer()
        self.strict = StrictScreener(budget_wei=int(budget_eth * 1e18))
        self.labeller = Labeller()
        self.alloc = Allocator(initial_eth=budget_eth, budget_eth=budget_eth)
        self.hub = Hub()
        self.live = live
        self.relay = FlashbotsRelay(Rpc(rpc_url())) if live else None
        self.fees = _resolve_fee_heads()

    async def on_pending(self, rpc: Rpc, row: dict) -> None:
        """Decode a pending swap, score, screen, risk-gate, then commit (virtual) capital."""
        # Fill reserves via getReserves (a single eth_call per pending swap is the live cost).
        x, y = await self._reserves(rpc, row.get("pool"))
        if x <= 0 or y <= 0:
            return
        row.update(x_pre=x, y_pre=y, seen_ms=int(time.time() * 1000), **self.fees)
        p = self.scorer.score(row)
        row["p"] = p
        from eth_mev.metrics.exposure import exposure as _exposure
        from eth_mev.metrics.exposure import _swap_from_row
        try:
            e = _exposure(_swap_from_row(row), x=x, y=y)
        except Exception:
            return
        row.update(slack=e.slack, mesp=e.mesp)
        strict_rows = self.strict.add(row.get("block") or 0, [row])
        if not strict_rows:
            return
        for sr in strict_rows:
            risks = event_risks(sr)
            accept, _reasons = gate({"safe": True, "risks": []}, risks)
            if accept:
                self.alloc.step(sr.get("block") or 0, [sr])
        self.store.pending([row])
        self.hub.update(self.alloc.state())
        self.hub.publish({"kind": "pending", "p": p, "pool": row.get("pool")})

    async def on_head(self, rpc: Rpc, head: dict) -> None:
        """Settle the block a newHeads header confirms: decode its swaps, label sandwiches.

        The header's ``number`` is the *new* block; its predecessor (``number-1``) is now final, so
        we fetch that block's full txs + receipts, decode Swap events, and run the inclusion-order
        sandwich labeller. This is the ground-truth generator for phase 7 and the finetune replay.
        """
        try:
            number = int(head.get("number", "0x0"), 16)
        except (TypeError, ValueError):
            return
        if number < 1:
            return
        prev = number - 1
        try:
            blk = await rpc.block_by_number(prev, full=True)
        except RuntimeError:
            return
        if not isinstance(blk, dict) or not blk.get("transactions"):
            self.hub.update(self.alloc.state())
            return
        swaps, owners = await self._decode_block(rpc, prev, blk)
        # label + persist + feed the allocator's settlement
        result = self.labeller.block(prev, swaps, owners)
        self.alloc.set_settlement(self.labeller.settlement)
        self.alloc.step(prev, [])
        self._persist_block(prev, swaps, result)
        self.hub.update(self.alloc.state())
        self.hub.publish({"kind": "settled_block", "block": prev, "n_swaps": len(swaps),
                          "n_sandwiches": len(result["sandwiches"])})

    async def _decode_block(self, rpc: Rpc, number: int, blk: dict) -> tuple[list, dict]:
        """Decode a confirmed block's Swap logs into ``Swap`` records + tx-owner map."""
        from eth_mev.chain.swaps.model import decode_swap_logs
        from eth_mev.chain.swaps.model import Swap
        owners: dict[str, str] = {}
        swaps: list[Swap] = []
        txs = blk.get("transactions") or []
        for i, tx in enumerate(txs):
            if not isinstance(tx, dict):
                continue
            n = normalized_tx(tx)
            owners[n["hash"]] = n["from"]
            # receipt for logs (Swap events); skip if the tx is not a router call to save rpc calls
            try:
                receipt = await rpc.transaction_receipt(n["hash"])
            except RuntimeError:
                receipt = None
            logs = (receipt or {}).get("logs") or []
            decoded = decode_swap_logs(n["hash"], number, i, logs, WETH)
            for s in decoded:
                s.user = n["from"]
                swaps.append(s)
        return swaps, owners

    def _persist_block(self, number: int, swaps: list, result: dict) -> None:
        import pandas as pd
        rows = []
        for s in swaps:
            rows.append({"tx_hash": s.tx_hash, "block_number": number, "tx_index": s.tx_index,
                         "log_index": s.log_index, "pool": s.pool, "venue": s.venue,
                         "token0": s.token0, "token1": s.token1, "user": s.user,
                         "is_buy": s.is_buy, "amount_in": s.amount_in, "amount_out": s.amount_out,
                         "x_pre": s.x_pre, "y_pre": s.y_pre, "fee_bps": s.fee_bps})
        self.store.swaps(pd.DataFrame(rows), number)
        # sandwich rows
        for s in result["sandwiches"]:
            self.store.sandwich(
                [s.key, number, s.pool, s.venue, s.attacker, s.profit, len(s.victims),
                 int(time.time() * 1000)],
                [[s.key, v.tx_hash, v.log_index, number, s.pool, v.is_buy,
                  getattr(v, "p", None), "sandwiched", s.attacker, s.profit] for v in s.victims])

    async def _reserves(self, rpc: Rpc, pool: str) -> tuple[float, float]:
        if not pool:
            return 0.0, 0.0
        data = "0x0902f1ac"  # getReserves() selector
        try:
            raw = await rpc.call_view(pool, data)
            if not raw or raw == "0x":
                return 0.0, 0.0
            from eth_abi import decode
            r0, r1, _ts = decode(["uint112", "uint112", "uint32"], bytes.fromhex(raw[2:]))
            return float(r0), float(r1)
        except Exception:
            return 0.0, 0.0

    def snapshot(self) -> dict:
        return {"allocator": self.alloc.state(), "strict": self.strict.stats(),
                "live": self.live, "fees": self.fees}


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

    from aiohttp import web
    app = build_app(screener.hub)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", a.port)
    await site.start()

    async with Rpc(rpc_url_str) as rpc:
        screener.fees = await _fees(rpc)
        ingest = MempoolIngest(rpc, ws, numeraire=WETH)
        ingest.on_pending = lambda row: screener.on_pending(rpc, row)
        ingest.on_head = lambda head: screener.on_head(rpc, head)
        ingest_task = asyncio.create_task(ingest.run())
        print(f"dry-run screener live{f' (LIVE MODE)' if live else ''} on http://localhost:{a.port}")
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
