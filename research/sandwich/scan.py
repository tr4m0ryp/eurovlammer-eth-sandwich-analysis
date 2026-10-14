"""Decode pulled blocks into swap and sandwich tables (Parquet), one row per swap.

Each swap row carries its exposure at the attacker-free state: for a labelled victim that is the
pre-state of its front-run, for everything else its own pre-state.

    .venv/bin/python -m research.sandwich.scan --raw data/raw/blocks_20261014 --out data/interim/sw_20261014
"""
from __future__ import annotations

import argparse
import gzip
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import orjson
import pandas as pd

from tlive.chain.swaps import decode_tx
from tlive.metrics.exposure import exposure
from tlive.metrics.sandwich import find


def block_rows(blk: dict) -> list[dict]:
    slot, bt = blk["slot"], blk["block_time"]
    swaps, signer, txf = [], {}, {}
    for tx in blk["txs"]:
        f, sw = decode_tx(tx, slot, tx["tx_index"], bt)
        signer[f.signature] = f.signer
        txf[f.signature] = f
        swaps += sw
    sands = find(swaps, signer)
    role: dict[tuple[str, int], tuple[str, int, object]] = {}
    for k, s in enumerate(sands):
        role[(s.front.signature, s.front.ev_index)] = ("front", k, s)
        role[(s.back.signature, s.back.ev_index)] = ("back", k, s)
        for v in s.victims:
            role[(v.signature, v.ev_index)] = ("victim", k, s)
    rows = []
    n_in_slot = len(blk["txs"])
    for s in swaps:
        f = txf[s.signature]
        rl, k, sw = role.get((s.signature, s.ev_index), ("", -1, None))
        x, y = (sw.front.x_pre, sw.front.y_pre) if rl == "victim" else (s.x_pre, s.y_pre)
        e = exposure(s, x, y)
        e_own = exposure(s) if rl == "victim" else e
        rows.append({
            "slot": slot, "block_time": bt, "leader": blk["leader"], "tx_index": s.tx_index,
            "ev_index": s.ev_index, "pos": s.tx_index / max(blk["n_tx"], 1), "n_venue_tx": n_in_slot,
            "signature": s.signature, "signer": f.signer, "user": s.user, "venue": s.venue,
            "pool": s.pool, "mint": s.mint, "is_buy": s.is_buy, "sol": s.sol, "tok": s.tok,
            "x_pre": x, "y_pre": y, "fee_bps": s.fee_bps, "kind": s.kind, "ix": s.ix_name,
            "amount": s.amount, "limit": s.limit, "router": f.router, "cu_price": f.cu_price,
            "cu_limit": f.cu_limit, "cu_used": f.cu_used, "priority_fee": f.priority_fee,
            "jito_tip": f.jito_tip, "n_transfers": len(f.transfers),
            "transfer_max": max((t[1] for t in f.transfers), default=0),
            "transfer_to": max(f.transfers, key=lambda t: t[1])[0] if f.transfers else "",
            "transfers": [t[0] for t in f.transfers],
            "transfer_amts": [t[1] for t in f.transfers],
            "slack": e.slack, "mesp": e.mesp, "front_opt": e.front, "slack_exec": e_own.slack,
            "role": rl, "sandwich": f"{slot}:{k}" if k >= 0 else "",
            "sw_link": sw.linked_by if sw else "", "sw_profit": sw.profit if sw else None,
            "sw_attacker": sw.attacker if sw else "", "sw_nvict": len(sw.victims) if sw else 0,
        })
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
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    todo = [p for p in sorted(a.raw.glob("*.jsonl.gz")) if not (a.out / f"{p.name[:-9]}.parquet").exists()]
    with ProcessPoolExecutor(a.workers) as ex:
        for p, df in zip(todo, ex.map(scan_file, todo)):
            df.to_parquet(a.out / f"{p.name[:-9]}.parquet", index=False)
            print(p.name, len(df), int((df.role == "victim").sum()) if len(df) else 0, flush=True)


if __name__ == "__main__":
    main()
