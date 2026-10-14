"""Morning export: every risk swap collected since ``--since``, with the night's final labels.

Reads the risk Parquet of every run directory (all restarts of the night), de-duplicates, and
recomputes **top 10%** over the whole night (``mesp_best`` >= the night's 90th percentile).
Writes ``data/exports/risk_<since>.parquet`` and a ``.csv`` twin, and prints the summary.

    .venv/bin/python -m tlive.store.export --since 2026-10-14T18:00
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]


def export(since: str, out_dir: Path = ROOT / "data" / "exports") -> Path:
    t0 = int(dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc).timestamp())
    files = sorted(str(p) for p in (ROOT / "data" / "live").glob("live_*/risk/*.parquet"))
    if not files:
        raise SystemExit("no risk files yet")
    con = duckdb.connect(config={"memory_limit": "2GB", "threads": 2})
    con.execute(f"""create table r as select distinct on (signature, ev_index) * exclude (top10)
                   from read_parquet({files}, union_by_name=true) where block_time >= {t0}""")
    # Rows written before the wallet-balance bound (2026-10-14 21:2x UTC) can carry a "no limit"
    # u64 max spend as gain (~1.8e10 SOL). No real wallet holds 1,000 SOL of overpayment for a
    # memecoin buy, so such rows are flagged and their gain set to null, never silently kept.
    con.execute("alter table r add column gain_unbounded boolean")
    con.execute("update r set gain_unbounded = mesp_best > 1e12")
    con.execute("update r set mesp_best = null, gain_pct = null, gain_roi = null where gain_unbounded")
    q90 = con.execute("select quantile_cont(mesp_best, 0.9) from r").fetchone()[0]
    con.execute(f"""create table out as select *, coalesce(mesp_best >= {q90}, false) as top10,
                   mesp_best / 1e9 as gain_sol, sw_profit / 1e9 as taken_sol,
                   'https://solscan.io/tx/' || signature as tx_url from r order by slot, ev_index""")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"risk_{since.replace(':', '').replace('-', '')}"
    con.execute(f"copy out to '{stem}.parquet' (format parquet)")
    con.execute(f"copy out to '{stem}.csv' (header)")
    print(con.execute("""select count(*) risk_swaps, sum((status='sandwiched')::int) sandwiched,
        round(avg((status='sandwiched')::int) * 100, 2) pct_sandwiched, sum(top10::int) top10, sum(gain_unbounded::int) gain_unbounded_rows,
        sum((top10 and status='sandwiched')::int) top10_sandwiched, round(sum(gain_sol), 2) gain_on_table_sol,
        round(sum(taken_sol) filter (where status='sandwiched'), 3) taken_sol_by_victim_row from out""").df().to_string(index=False))
    print(f"q90 gain = {q90 / 1e9:.4f} SOL; wrote {stem}.parquet and .csv")
    return stem


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="UTC, ISO format")
    export(ap.parse_args().since)
