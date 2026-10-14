"""Pull a contiguous range of confirmed blocks and keep the pump.fun / PumpSwap transactions.

Each block is one JSON line: slot, block_time, leader, n_tx (all, votes included) and the kept
transactions with their true in-block index. Raw records are the ground truth for the sandwich
labels, so nothing is decoded or dropped here beyond the venue filter.

    .venv/bin/python -m research.sandwich.pull --slots 27000 --out data/raw/blocks_20261014
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time
from pathlib import Path

from tlive.chain.programs import PUMP, PUMPSWAP
from tlive.ingest.rpc import Rpc, rpc_url

KEEP = (PUMP, PUMPSWAP)


def keep_txs(block: dict) -> list[dict]:
    out = []
    for i, tx in enumerate(block.get("transactions") or ()):
        meta = tx.get("meta") or {}
        la = meta.get("loadedAddresses") or {}
        keys = tx["transaction"]["message"]["accountKeys"] + la.get("writable", []) + la.get("readonly", [])
        if any(k in keys for k in KEEP):
            tx["tx_index"] = i
            out.append(tx)
    return out


async def pull(start: int, n: int, out: Path, chunk: int = 500) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with Rpc(rpc_url(), concurrency=24) as rpc:
        for c0 in range(start, start + n, chunk):
            path = out / f"{c0}.jsonl.gz"
            if path.exists():
                continue
            m = min(chunk, start + n - c0)
            t = time.time()
            leaders, *blocks = await asyncio.gather(rpc.leaders(c0, m), *[rpc.block(s) for s in range(c0, c0 + m)])
            tmp = path.with_suffix(".tmp")
            kept = 0
            with gzip.open(tmp, "wt") as f:
                for j, b in enumerate(blocks):
                    if b is None:
                        continue
                    txs = keep_txs(b)
                    kept += len(txs)
                    f.write(json.dumps({"slot": c0 + j, "block_time": b.get("blockTime"),
                                        "leader": leaders[j] if j < len(leaders) else None,
                                        "n_tx": len(b.get("transactions") or ()), "txs": txs}) + "\n")
            tmp.rename(path)
            print(f"{c0} blocks={sum(b is not None for b in blocks)} kept={kept} "
                  f"{time.time() - t:.1f}s calls={rpc.n_calls} retry={rpc.n_retry}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=27000)
    ap.add_argument("--end", type=int, default=0, help="last slot (default: confirmed tip - 200)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    async def run():
        end = a.end
        if not end:
            async with Rpc(rpc_url()) as rpc:
                end = await rpc.slot() - 200
        start = end - a.slots
        start -= start % 500
        await pull(start, a.slots, a.out)
    asyncio.run(run())


if __name__ == "__main__":
    main()
