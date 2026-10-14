"""Stage 1: decode pulled blocks into one swap table (Parquet per chunk), no labels yet.

    .venv/bin/python -m research.sandwich.scan --raw data/raw/blocks_20261014 --out data/interim/swaps_20261014
"""
from __future__ import annotations

import argparse
import gzip
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import orjson
import pandas as pd

from tlive.chain.swaps import decode_tx


def block_rows(blk: dict) -> list[dict]:
    slot, bt = blk["slot"], blk["block_time"]
    rows = []
    for tx in blk["txs"]:
        f, sw = decode_tx(tx, slot, tx["tx_index"], bt)
        for s in sw:
            r = asdict(s)
            r.update(leader=blk["leader"], n_block_tx=blk["n_tx"], signer=f.signer, router=f.router,
                     cu_price=f.cu_price, cu_limit=f.cu_limit, cu_used=f.cu_used,
                     priority_fee=f.priority_fee, jito_tip=f.jito_tip,
                     transfers=[t[0] for t in f.transfers], transfer_amts=[t[1] for t in f.transfers])
            rows.append(r)
    return rows


def scan_file(path: Path) -> pd.DataFrame:
    rows = []
    with gzip.open(path, "rb") as fh:
        for line in fh:
            rows += block_rows(orjson.loads(line))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    todo = [p for p in sorted(a.raw.glob("*.jsonl.gz")) if not (a.out / f"{p.name[:-9]}.parquet").exists()]
    with ProcessPoolExecutor(a.workers) as ex:
        for p, df in zip(todo, ex.map(scan_file, todo)):
            df.to_parquet(a.out / f"{p.name[:-9]}.parquet", index=False)
            print(p.name, len(df), flush=True)


if __name__ == "__main__":
    main()
