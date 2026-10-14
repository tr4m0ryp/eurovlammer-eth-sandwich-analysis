"""Artifact documents for the Visibility tab: which optimal targets were visible to attackers.

Reads the ``optimal`` table from ``data/screener.duckdb`` and measures, over the optimal set,
what visibility an attacker had into each buy. Visibility is inferred from the submission path
and pool momentum — Solana has no public mempool, so direct observation is never guaranteed.

Three visibility tiers:
  **high**   direct, untipped, on a hot pool
  **medium** one of: routed (aggregator or other), tipped, or quiet pool
  **low**    two or more of the above

The tab shows only orders with at least medium visibility — the ones an attacker could plausibly
have seen before inclusion.

    .venv/bin/python -m tlive.store.visibility_push
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "artifact" / "visibility"
ROWS_PER_DOC = 900
AGGREGATORS = ("JUP6LkbZ", "6Vo3245e", "term9YPb", "L2TExMFKdjpN")
HOT_THRESHOLD = -2.071          # log10 prior same-side flow / reserve: 0.85% of the pool
COLS = ["rank", "block_time", "venue", "sol", "mc_sol", "pct_mc", "fee_bps", "slack",
        "gain_real", "net_real", "hot", "route_type", "tipped", "visibility",
        "sandwiched", "taken_sol", "signature"]


def q(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build(db: Path = ROOT / "data" / "screener.duckdb") -> list[Path]:
    con = duckdb.connect(str(db), read_only=True)
    # Build visibility tiers over the optimal set
    # Handle NULL log_flow_rel: treat as quiet pool (conservative visibility estimate)
    df = con.execute(f"""select optimal_rank as rank, block_time, venue, sol, mc_sol, size_mc as pct_mc,
        fee_bps, slack, gain_real, net_real,
        coalesce(log_flow_rel, -999) > {HOT_THRESHOLD} as hot,
        case when router = '' then 'direct'
             when left(router, 8) in {AGGREGATORS} then 'aggregator'
             else 'other_router' end as route_type,
        coalesce(jito_tip, 0) > 0 as tipped,
        case when coalesce(log_flow_rel, -999) > {HOT_THRESHOLD} and router = '' and coalesce(jito_tip, 0) = 0 then 'high'
             when coalesce(log_flow_rel, -999) > {HOT_THRESHOLD} and (router != '' or coalesce(jito_tip, 0) > 0) then 'medium'
             when coalesce(log_flow_rel, -999) <= {HOT_THRESHOLD} and router = '' and coalesce(jito_tip, 0) = 0 then 'medium'
             else 'low' end as visibility,
        (status = 'sandwiched')::int as sandwiched,
        sw_profit / 1e9 as taken_sol, signature
        from optimal where optimal order by optimal_rank""").df()

    summary = {}
    for tier in ("high", "medium", "low"):
        sub = df[df.visibility == tier]
        summary[tier] = {
            "n": len(sub),
            "hit_rate": float(sub.sandwiched.mean()) if len(sub) else 0.0,
            "net_sol": float(sub.net_real.sum() / 1e9) if len(sub) else 0.0,
            "taken_sol": float(sub.taken_sol.sum()) if len(sub) else 0.0,
        }

    # Breakdown by route × tip × pool for the detail cards
    detail = q(con, f"""select
        case when router = '' then 'direct'
             when left(router, 8) in {AGGREGATORS} then 'aggregator'
             else 'other_router' end as route_type,
        (coalesce(jito_tip, 0) > 0) as tipped,
        (coalesce(log_flow_rel, -999) > {HOT_THRESHOLD}) as hot,
        case when coalesce(log_flow_rel, -999) > {HOT_THRESHOLD} and router = '' and coalesce(jito_tip, 0) = 0 then 'high'
             when coalesce(log_flow_rel, -999) > {HOT_THRESHOLD} and (router != '' or coalesce(jito_tip, 0) > 0) then 'medium'
             when coalesce(log_flow_rel, -999) <= {HOT_THRESHOLD} and router = '' and coalesce(jito_tip, 0) = 0 then 'medium'
             else 'low' end as visibility,
        count(*) n, avg((status = 'sandwiched')::int) hit_rate,
        sum(net_real) / 1e9 net_sol
        from optimal where optimal group by all order by hit_rate desc""")

    # Write only visible (high + medium) rows to the document chunks
    visible = df[df.visibility != "low"].sort_values("rank")
    rows = [[
        int(r["rank"]), int(r["block_time"]), "p" if r["venue"] == "pump" else "s", round(r["sol"] / 1e9, 4),
        round(r["mc_sol"], 1), round(r["pct_mc"] * 100, 3), int(r["fee_bps"]),
        round(min(r["slack"], 10), 4) if r["slack"] is not None else None,
        round(r["gain_real"] / 1e9, 5), round(r["net_real"] / 1e9, 5),
        bool(r["hot"]), r["route_type"], bool(r["tipped"]), r["visibility"],
        bool(r["sandwiched"]), round(r["taken_sol"], 5) if r["taken_sol"] and not (isinstance(r["taken_sol"], float) and (r["taken_sol"] != r["taken_sol"])) else None, r["signature"]
    ] for _, r in visible.iterrows()]

    OUT.mkdir(parents=True, exist_ok=True)
    chunks = [rows[i:i + ROWS_PER_DOC] for i in range(0, len(rows), ROWS_PER_DOC)]
    meta = {
        "columns": COLS, "n_docs": len(chunks), "n_total": len(df),
        "n_visible": len(rows), "summary": summary, "detail": detail,
    }
    paths = [OUT / "summary.json"]
    paths[0].write_text(json.dumps(meta, separators=(",", ":"), default=float))
    for k, c in enumerate(chunks):
        p = OUT / f"rows_{k:03d}.json"
        p.write_text(json.dumps({"k": k, "rows": c}, separators=(",", ":"), default=float))
        paths.append(p)
    con.close()
    return paths


if __name__ == "__main__":
    ps = build()
    s = json.loads(ps[0].read_text())
    print(f"{len(ps)} docs; summary:")
    for tier, v in s["summary"].items():
        print(f"  {tier}: {v['n']} buys, {v['hit_rate']*100:.1f}% hit, {v['net_sol']:.2f} SOL net")
    print(f"Visible (high + medium): {s['n_visible']} of {s['n_total']} optimal targets")