"""Artifact documents for the Optimal tab: the gate funnel, the found patterns, and every optimal row.

Reads the ``optimal`` table (research/sandwich/optimal.py) from ``data/screener.duckdb`` and writes
``data/artifact/optimal/summary.json`` plus ``rows_NNN.json`` chunks (compact arrays).

    .venv/bin/python -m tlive.store.optimal_push
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "artifact" / "optimal"
ROWS_PER_DOC = 900
HOT = -2.071          # log10 prior same-side flow / reserve: the tree's split (0.85% of the pool)
COLS = ["signature", "block_time", "venue", "sol", "mc_sol", "pct_mc", "fee_bps", "slack", "gain_pred",
        "gain_real", "net_real", "rank", "hot", "route", "sandwiched", "taken_sol", "p"]


def q(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build(db: Path = ROOT / "data" / "screener.duckdb") -> list[Path]:
    con = duckdb.connect(str(db), read_only=True)
    funnel = q(con, """select count(*) risk, sum(realistic::int) realistic, sum((realistic and profitable)::int) profitable,
        sum(qualified::int) qualified, sum(optimal::int) optimal, min(net_real) filter (where optimal) / 1e9 cutoff_sol,
        sum(net_real) filter (where optimal) / 1e9 optimal_net_sol,
        avg((status='sandwiched')::int) filter (where optimal) optimal_hit,
        avg((status='sandwiched')::int) filter (where qualified and not optimal) rest_hit from optimal""")[0]
    rules = q(con, """select rule, count(*) n, sum(optimal::int) n_opt, avg(optimal::int) prec,
        sum(optimal::int) / (select sum(optimal::int) from optimal) rec from (select optimal,
        case when sol > 1.45e9 and size_mc > 0.0056 then 'A' when sol <= 1.45e9 and size_mc > 0.0327 and slack > 0.333
        then 'B' else 'other' end as rule from optimal where qualified) group by 1 order by 1""")
    attack = q(con, f"""select (log_flow_rel > {HOT}) as hot, sol > 9.855e9 as whale, count(*) n,
        avg((status='sandwiched')::int) hit, sum(net_real) / 1e9 net_sol from optimal where optimal group by all order by 1, 2""")
    routes = q(con, """select case when router = '' then 'direct' else left(router, 8) end as route, count(*) n,
        avg((status='sandwiched')::int) hit from optimal where optimal group by 1 order by n desc limit 8""")
    profile = q(con, """select optimal, count(*) n, median(sol) / 1e9 buy_sol, median(size_mc) * 100 pct_mc,
        median(mc_sol) mc_sol, avg((slack >= 10)::int) no_limit, avg((venue='pump')::int) on_curve,
        avg((jito_tip > 0)::int) tip from optimal where qualified group by 1 order by 1 desc""")
    rows = con.execute(f"""select signature, block_time, case when venue='pump' then 'p' else 's' end, round(sol/1e9, 4),
        round(mc_sol, 1), round(size_mc*100, 3), fee_bps::int, round(least(slack, 10), 4), round(mesp_best/1e9, 5),
        round(gain_real/1e9, 5), round(net_real/1e9, 5), optimal_rank, (log_flow_rel > {HOT})::int,
        case when router = '' then 'direct' else left(router, 8) end, (status='sandwiched')::int,
        round(sw_profit/1e9, 5), round(p, 4) from optimal where optimal order by optimal_rank""").fetchall()
    con.close()
    OUT.mkdir(parents=True, exist_ok=True)
    chunks = [rows[i:i + ROWS_PER_DOC] for i in range(0, len(rows), ROWS_PER_DOC)]
    summary = {"columns": COLS, "n_docs": len(chunks), "funnel": funnel, "rules": rules, "attack": attack,
               "routes": routes, "profile": profile}
    paths = [OUT / "summary.json"]
    paths[0].write_text(json.dumps(summary, separators=(",", ":"), default=float))
    for k, c in enumerate(chunks):
        p = OUT / f"rows_{k:03d}.json"
        p.write_text(json.dumps({"k": k, "rows": c}, separators=(",", ":"), default=float))
        paths.append(p)
    return paths


if __name__ == "__main__":
    ps = build()
    print(len(ps), "docs; largest", max(p.stat().st_size for p in ps) // 1024, "KiB")
    print(json.dumps(json.loads(ps[0].read_text())["funnel"]))
