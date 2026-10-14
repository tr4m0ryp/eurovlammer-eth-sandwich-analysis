"""Artifact document for the Exposure tab: what lowered an optimal target's chance of being sandwiched.

Read-only and retrospective. Reads the ``optimal`` table (data/screener.duckdb) and measures, over the
optimal set, the attacked rate under each protection a buyer actually controls (a slippage limit, a
protected submission, a routed order), and the attacked rate by prior pool momentum. No capture, no
execution: this is the defensive mirror of the Optimal patterns.

    .venv/bin/python -m tlive.store.exposure_push
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "artifact" / "exposure"
ROUTED = ("JUP6LkbZ", "6Vo3245e", "term9YPb")      # routers with the lowest attacked rate in the data


def q(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build(db: Path = ROOT / "data" / "screener.duckdb") -> Path:
    con = duckdb.connect(str(db), read_only=True)
    con.execute(f"""create temp view o as select *,
        (slack < 10) as has_limit, (jito_tip > 0) as protected_submit,
        (left(router, 8) in {ROUTED}) as routed, (status = 'sandwiched')::int as attacked from optimal where optimal""")
    base = con.execute("select avg(attacked), count(*) from o").fetchone()
    shields = []
    for label, col in [("A slippage limit (not unlimited)", "has_limit"),
                       ("Protected submission (Jito tip)", "protected_submit"),
                       ("Routed through an aggregator", "routed")]:
        on = con.execute(f"select avg(attacked), count(*) from o where {col}").fetchone()
        off = con.execute(f"select avg(attacked) from o where not {col}").fetchone()[0]
        drop = 1 - on[0] / off if off else None
        shields.append({"label": label, "n": on[1], "attacked": on[0], "vs_without": off, "drop": drop})
    flow = q(con, """select case when log_flow_rel > -1 then 'e above 10% of pool'
        when log_flow_rel > -1.5 then 'd 3-10%' when log_flow_rel > -2.071 then 'c 0.85-3%'
        when log_flow_rel > -3 then 'b 0.1-0.85%' else 'a under 0.1%' end as band,
        count(*) n, avg(attacked) attacked from o group by 1 order by 1""")
    summary = {"base_rate": base[0], "n_optimal": base[1], "shields": shields, "flow": flow,
               "note": "Solana has no public mempool; pre-inclusion visibility is inferred from the "
                       "submission path and pool momentum, not observed directly."}
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "summary.json"
    out.write_text(json.dumps(summary, separators=(",", ":"), default=float))
    con.close()
    return out


if __name__ == "__main__":
    p = build()
    print(p, p.stat().st_size, "bytes")
    print(json.dumps(json.loads(p.read_text())["shields"], indent=1))
