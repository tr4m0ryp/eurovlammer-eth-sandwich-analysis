"""Copy the night's risk table from ``data/screener.duckdb`` into artifact-database documents.

The artifact cannot open a local file, so the analysis data is shipped as documents: one
``night/summary`` (totals, size-vs-MC deciles, hourly counts) and ``night/rows_NNN`` chunks of the
risk rows as compact arrays (column order in ``summary.columns``), each under the 256 KiB cap.

    .venv/bin/python -m tlive.store.artifact_push      # writes data/artifact/night/*.json
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "artifact" / "night"
ROWS_PER_DOC = 900
COLS = ["signature", "slot", "block_time", "venue", "sol", "mc_sol", "size_mc_pct", "fee_bps", "slack",
        "gain_sol", "gain_pct", "gain_roi", "top10", "sandwiched", "taken_sol", "p", "gain_unbounded"]


def build(db: Path = ROOT / "data" / "screener.duckdb") -> list[Path]:
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("""select signature, slot, block_time, case when venue='pump' then 'p' else 's' end,
        round(sol/1e9, 5), round(mc_sol, 2), round(size_mc*100, 4), fee_bps::int,
        case when slack is null or isnan(slack) then null when slack > 10 then 10 else round(slack, 4) end,
        round(mesp_best/1e9, 6), round(gain_pct, 4), round(gain_roi, 4), top10::int,
        (status='sandwiched')::int, round(sw_profit/1e9, 6), round(p, 4), gain_unbounded::int
        from risk order by slot, ev_index""").fetchall()
    s = con.execute("""select count(*), sum((status='sandwiched')::int), sum(top10::int),
        sum((top10 and status='sandwiched')::int), sum(gain_unbounded::int), min(block_time), max(block_time),
        quantile_cont(mesp_best, 0.9)/1e9, median(mesp_best)/1e9, sum(mesp_best)/1e9,
        sum(sw_profit) filter (where status='sandwiched')/1e9 from risk""").fetchone()
    deciles = con.execute("""select d, count(*), min(size_mc)*100, avg((status='sandwiched')::int), median(mesp_best)/1e9
        from (select *, ntile(10) over (order by size_mc) d from risk where size_mc is not null) group by d order by d""").fetchall()
    hourly = con.execute("""select (block_time // 3600) * 3600 h, count(*), sum((status='sandwiched')::int)
        from risk where block_time is not null group by h order by h""").fetchall()
    sands = con.execute("""select count(*), median(profit)/1e9, sum(profit)/1e9, count(distinct attacker)
        from sandwiches""").fetchone()
    OUT.mkdir(parents=True, exist_ok=True)
    chunks = [rows[i:i + ROWS_PER_DOC] for i in range(0, len(rows), ROWS_PER_DOC)]
    summary = {"columns": COLS, "n_docs": len(chunks), "n": s[0], "sandwiched": s[1], "top10": s[2],
               "top10_sandwiched": s[3], "gain_unbounded": s[4], "t0": s[5], "t1": s[6], "q90_sol": s[7],
               "median_gain_sol": s[8], "gain_total_sol": s[9], "taken_by_victim_row_sol": s[10],
               "sandwiches": {"n": sands[0], "median_profit_sol": sands[1], "total_profit_sol": sands[2],
                              "attackers": sands[3]},
               "size_mc_deciles": [{"d": d, "n": n, "lo_pct": lo, "rate": r, "median_gain_sol": g}
                                   for d, n, lo, r, g in deciles],
               "hourly": [{"t": h, "n": n, "sandwiched": k} for h, n, k in hourly]}
    paths = [OUT / "summary.json"]
    paths[0].write_text(json.dumps(summary, separators=(",", ":"), default=float))
    for k, c in enumerate(chunks):
        p = OUT / f"rows_{k:03d}.json"
        p.write_text(json.dumps({"k": k, "rows": c}, separators=(",", ":"), default=float))
        paths.append(p)
    con.close()
    return paths


if __name__ == "__main__":
    ps = build()
    print(len(ps), "docs; largest", max(p.stat().st_size for p in ps) // 1024, "KiB")
