"""Ground-truth collector: stream confirmed blocks, decode V2 Swap events, detect sandwiches.

Phase 7: run this for a few hours to persist the inclusion-order settlement DuckDB that phase 8's
finetune replays against. Read-only — subscribes ``newHeads``, fetches each block + its receipts,
decodes Uniswap V2 (and fork) ``Swap`` events, runs the mev-inspect-style sandwich labeller, and
persists to ``data/live_*/live.duckdb`` (``included_swaps`` + ``sandwiches`` + ``victims``).

Usage:
    ETH_WS_URL=wss://eth.drpc.org ETH_RPC_URL=https://eth.drpc.org \
      .venv/bin/python scripts/collect_ground_truth.py --hours 3
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from eth_mev.chain.v2.swaps import decode_swap_logs, decode_v2_router
from eth_mev.chain.v2.constants import ROUTERS as _ROUTERS
from eth_mev.chain.tx import normalized_tx
from eth_mev.ingest.rpc import Rpc, Subscriber
from eth_mev.metrics.sandwich import Sandwich, find_sandwiches, top_heavy
from eth_mev.store.writer import Store

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


class Collector:
    def __init__(self, run_dir: Path, rpc: Rpc, ws_url: str):
        self.store = Store(run_dir, {"started_ms": int(time.time() * 1000), "kind": "ground_truth"})
        self.rpc = rpc
        self.ws_url = ws_url
        self.n_blocks = self.n_swap_logs = 0
        self.sandwiches: list[Sandwich] = []
        self.last_head = 0.0
        self.reserves: dict[str, tuple[float, float]] = {}   # pool -> (x_pre, y_pre) cache

    async def _reserves(self, pool: str) -> tuple[float, float]:
        """getReserves() for a pool, cached per pool (reserves change per swap, but the pre-state
        for the *first* swap in a block is what matters for exposure; we store the latest)."""
        if pool in self.reserves:
            return self.reserves[pool]
        try:
            raw = await self.rpc.call_view(pool, "0x0902f1ac")   # getReserves()
            if not raw or raw == "0x":
                return 0.0, 0.0
            from eth_abi import decode
            r0, r1, _ts = decode(["uint112", "uint112", "uint32"], bytes.fromhex(raw[2:]))
            self.reserves[pool] = (float(r0), float(r1))
            return self.reserves[pool]
        except Exception:
            return 0.0, 0.0

    async def settle_block(self, number: int) -> None:
        """Fetch + decode one confirmed block, detect sandwiches, persist."""
        try:
            blk = await self.rpc.block_by_number(number, full=True)
        except RuntimeError:
            return
        if not isinstance(blk, dict) or not blk.get("transactions"):
            return
        txs = blk.get("transactions") or []
        owners: dict[str, str] = {}
        # (tx_index, tx_hash, origin, logs, calldata_desc or None)
        swaps_by_tx: list[tuple[int, str, str, list, dict | None]] = []
        for txi, tx in enumerate(txs):
            if not isinstance(tx, dict):
                continue
            n = normalized_tx(tx)
            owners[n["hash"]] = n["from"]
            # Only fetch receipts for txs that *could* touch a V2 pool (a router/aggregator call).
            # Cheap heuristic: non-empty input + a contract target. Skip plain ETH transfers.
            if not n["to"] or not n["input"] or n["input"] in ("0x", ""):
                continue
            calldata = decode_v2_router({"to": n["to"], "input": tx.get("input"),
                                         "hash": n["hash"], "from": n["from"],
                                         "transactionIndex": txi}, _ROUTERS) or [None]
            try:
                receipt = await self.rpc.transaction_receipt(n["hash"])
            except RuntimeError:
                receipt = None
            logs = (receipt or {}).get("logs") or []
            if not logs:
                continue
            swaps_by_tx.append((txi, n["hash"], n["from"], logs, calldata[0]))

        all_swaps = []
        rows = []
        for txi, tx_hash, origin, logs, calldata in swaps_by_tx:
            decoded = decode_swap_logs(tx_hash, number, txi, logs, numeraire="")
            for s in decoded:
                s.user = origin
                # Resolve pool reserves for the exposure pre-state (first swap in a block uses the
                # block-start reserves; subsequent swaps reuse the cached value as an approximation).
                x, y = await self._reserves(s.pool)
                s.x_pre, s.y_pre = x, y
                all_swaps.append(s)
                row = {"tx_hash": s.tx_hash, "block_number": number, "tx_index": s.tx_index,
                       "log_index": s.log_index, "pool": s.pool, "venue": s.venue,
                       "token0": s.token0, "token1": s.token1, "user": s.user,
                       "is_buy": s.is_buy, "amount_in": s.amount_in, "amount_out": s.amount_out,
                       "x_pre": x, "y_pre": y, "fee_bps": s.fee_bps,
                       "kind": None, "amount": None, "limit": None, "deadline": None}
                # Merge the victim's calldata guard (amountOutMin = the sandwich's room) when the
                # tx is a direct V2 router call and this is its only swap (single-hop).
                if calldata and calldata.get("ok"):
                    row["kind"] = calldata.get("kind")
                    row["amount"] = calldata.get("amount")
                    row["limit"] = calldata.get("limit")
                    row["deadline"] = calldata.get("deadline")
                    s.kind = calldata.get("kind", s.kind)
                    s.amount = calldata.get("amount")
                    s.limit = calldata.get("limit")
                rows.append(row)
        self.n_swap_logs += len(rows)

        sands = find_sandwiches(all_swaps, owners)
        for s in sands:
            self.store.sandwich(
                [s.key, number, s.pool, s.venue, s.attacker, s.profit, len(s.victims),
                 int(time.time() * 1000)],
                [[s.key, v.tx_hash, v.log_index, number, s.pool, v.is_buy,
                  None, "sandwiched", s.attacker, s.profit] for v in s.victims])
        self.sandwiches.extend(sands)
        self.n_blocks += 1

        # Persist included swaps to BOTH the DuckDB table (immediate settlement ground truth)
        # and the buffered parquet export.
        if rows:
            self.store.included(rows)
            import pandas as pd
            self.store.swaps(pd.DataFrame(rows), number)
        if self.n_swap_logs and self.n_swap_logs % 500 == 0:
            self.store.flush()

    async def run(self, hours: float, max_blocks: int | None) -> None:
        deadline = time.monotonic() + hours * 3600
        sub = Subscriber(self.ws_url, ["newHeads"])
        seen = 0
        try:
            async for msg in sub.messages():
                if time.monotonic() > deadline:
                    break
                if "error" in msg or "params" not in msg:
                    continue
                params = msg.get("params")
                if not isinstance(params, dict):
                    continue
                payload = params.get("result")
                if not (isinstance(payload, dict) and "number" in payload):
                    continue
                try:
                    number = int(payload["number"], 16)
                except (TypeError, ValueError):
                    continue
                # settle the *previous* block (the confirmed one), the header's number is the new tip
                if number >= 1:
                    await self.settle_block(number - 1)
                seen += 1
                self.last_head = time.monotonic()
                if max_blocks and self.n_blocks >= max_blocks:
                    break
                if seen % 20 == 0:
                    print(f"[collector] blocks={self.n_blocks} swap_logs={self.n_swap_logs} "
                          f"sandwiches={len(self.sandwiches)}", flush=True)
        finally:
            await sub.close()
            self.store.close()
        summ = top_heavy(self.sandwiches)
        print(f"[collector] done: {self.n_blocks} blocks, {self.n_swap_logs} V2 swap logs, "
              f"{len(self.sandwiches)} sandwiches across {summ['n_attackers']} attackers; "
              f"top-1 profit share {summ['share_top1']:.2%}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--max-blocks", type=int, default=None)
    ap.add_argument("--run-dir", type=str, default=None)
    a = ap.parse_args()
    from eth_mev.ingest.rpc import rpc_url, ws_url
    url = rpc_url()
    ws = ws_url() or url.replace("https://", "wss://").replace("http://", "ws://")
    run_dir = Path(a.run_dir) if a.run_dir else DATA_DIR / time.strftime("gt_%Y%m%d_%H%M%S")
    async with Rpc(url, rps=15, concurrency=12) as rpc:
        c = Collector(run_dir, rpc, ws)
        await c.run(a.hours, a.max_blocks)


if __name__ == "__main__":
    asyncio.run(main())
