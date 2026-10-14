"""Stage 2: label sandwiches across slots and attach each swap's exposure.

Exposure is measured at the attacker-free state: for a victim, the pool state just before the
first front-run swap; for everything else, its own pre-state. Output: one Parquet with every swap,
its role (front / back / victim / ""), the sandwich key, the attacker, and slack + MESP.

    .venv/bin/python -m research.sandwich.label --swaps data/interim/swaps_20261014 --out data/interim/labels_20261014.parquet
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import fields
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from tlive.chain.swaps.model import Swap
from tlive.metrics.exposure import exposure
from tlive.metrics.sandwich import entity_filter, find

SWAP_COLS = [f.name for f in fields(Swap)]


def to_swaps(df: pd.DataFrame) -> list[Swap]:
    recs = df[SWAP_COLS].to_dict("records")
    out = []
    for r in recs:
        for k in ("amount", "limit"):
            v = r[k]
            r[k] = None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)
        out.append(Swap(**r))
    return out


def _expo(args) -> list[tuple[float, float, float, float, float]]:
    recs, xs, ys = args
    res = []
    for r, x, y in zip(recs, xs, ys):
        s = Swap(**r)
        e = exposure(s, x, y)
        e_own = exposure(s)
        res.append((e.slack, e.mesp, e.front, e_own.slack, e_own.mesp))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--swaps", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    con = duckdb.connect(config={"memory_limit": "6GB", "threads": 4})
    df = con.sql(f"select distinct on (signature, ev_index) * from '{a.swaps}/*.parquet' "
                 "order by slot, tx_index, ev_index").df()
    swaps = to_swaps(df)
    wallet = dict(zip(df.signature, df.signer))
    sands = find(swaps, wallet)
    n_swaps = df.signer.value_counts().to_dict()
    bots = entity_filter(sands, n_swaps)
    idx = {(s.signature, s.ev_index): i for i, s in enumerate(swaps)}
    role = np.full(len(df), "", dtype=object)
    key = np.full(len(df), "", dtype=object)
    attacker = np.full(len(df), "", dtype=object)
    bot_ok = np.zeros(len(df), dtype=bool)
    profit = np.full(len(df), np.nan)
    x_ref, y_ref = df.x_pre.to_numpy().copy(), df.y_pre.to_numpy().copy()
    for sw in sands:
        for rl, legs in (("front", sw.front), ("back", sw.back), ("victim", sw.victims)):
            for s in legs:
                i = idx[(s.signature, s.ev_index)]
                if rl == "victim" and role[i] == "victim" and bot_ok[i]:
                    continue                      # keep the first entity-backed attribution
                role[i], key[i], attacker[i] = rl, sw.key, sw.attacker
                bot_ok[i], profit[i] = sw.attacker in bots, sw.profit
                if rl == "victim":
                    x_ref[i], y_ref[i] = sw.front[0].x_pre, sw.front[0].y_pre
    df["role"], df["sandwich"], df["attacker"], df["bot"], df["sw_profit"] = role, key, attacker, bot_ok, profit
    df["x_ref"], df["y_ref"] = x_ref, y_ref
    recs = df[SWAP_COLS].to_dict("records")
    for r in recs:
        for k in ("amount", "limit"):
            v = r[k]
            r[k] = None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)
    n = 20000
    jobs = [(recs[i:i + n], x_ref[i:i + n], y_ref[i:i + n]) for i in range(0, len(recs), n)]
    with ProcessPoolExecutor(a.workers) as ex:
        res = [t for part in ex.map(_expo, jobs) for t in part]
    df["slack"], df["mesp"], df["front_opt"], df["slack_exec"], df["mesp_exec"] = map(list, zip(*res))
    df.to_parquet(a.out, index=False)
    print(f"swaps={len(df)} sandwiches={len(sands)} bots={len(bots)} "
          f"victims={int((df.role == 'victim').sum())} bot_victims={int(((df.role == 'victim') & df.bot).sum())}")


if __name__ == "__main__":
    main()
