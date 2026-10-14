"""One DuckDB file for the analysis: the night's live runs plus the 24 h research labels.

    .venv/bin/python -m tlive.store.consolidate --since-run live_20261014_184148

Tables (``data/screener.duckdb``):
  risk              every settled risk buy (buy-side, gain above the two-transaction gas), one row per
                    swap, final night-wide ``top10``; rows whose gain came from a "no limit" u64 spend
                    before the wallet-balance bound have ``gain_unbounded`` and null gain fields
  swaps             every scored swap of those runs (features, model score, flag)
  sandwiches        every entity-filtered sandwich the live labeller settled, ``victims`` its victims
  flagged           model-flagged swaps with their settled status
  research_labels   the 24 h fleet sample (2026-10-13/14): every swap, role, attacker, exposure
  runs              which run directory each live row came from
Run databases and Parquet stay where they are: this file is a copy, never the only record.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "screener.duckdb"
GAIN_SANE = 1e12      # 1,000 SOL of overpayment on one memecoin buy: only the u64 "no limit" sentinel


def consolidate(since_run: str, out: Path = OUT) -> None:
    runs = sorted(p for p in (ROOT / "data" / "live").glob("live_*") if p.name >= since_run)
    if out.exists():
        out.rename(out.with_suffix(".duckdb.prev"))
    con = duckdb.connect(str(out), config={"memory_limit": "8GB", "threads": 4})
    con.execute("create table runs (run_id varchar, dir varchar)")
    for name in ("risk", "sandwiches", "victims", "flagged"):
        parts = []
        for i, r in enumerate(runs):
            db = r / "live.duckdb"
            if not db.exists():
                continue
            con.execute(f"attach '{db}' as r{i} (read_only)")
            if con.execute(f"select count(*) from duckdb_tables() where database_name='r{i}' and table_name='{name}'").fetchone()[0]:
                parts.append(f"select '{r.name}' as run_id, * from r{i}.{name}")
        if parts:
            con.execute(f"create table {name} as " + " union all by name ".join(parts))
        for i in range(len(runs)):
            con.execute(f"detach database if exists r{i}")
    con.executemany("insert into runs values (?, ?)", [[r.name, str(r)] for r in runs])
    con.execute(f"""create or replace table risk as
        select * exclude (top10_live) from (select *, row_number() over (partition by signature, ev_index order by run_id) k from risk) where k = 1""")
    con.execute("alter table risk drop column k")
    con.execute("alter table risk add column gain_unbounded boolean")
    con.execute(f"update risk set gain_unbounded = coalesce(mesp_best > {GAIN_SANE}, false)")
    con.execute("update risk set mesp_best = null, mesp = null, mesp_ref = null, gain_pct = null, gain_roi = null where gain_unbounded")
    q90 = con.execute("select quantile_cont(mesp_best, 0.9) from risk").fetchone()[0]
    con.execute(f"alter table risk add column top10 boolean")
    con.execute(f"update risk set top10 = coalesce(mesp_best >= {q90}, false)")
    swap_files = [str(p) for r in runs for p in (r / "swaps").glob("*.parquet")]
    if swap_files:
        con.execute(f"create table swaps as select * from read_parquet({swap_files}, union_by_name=true, filename=true)")
    labels = ROOT / "data" / "interim" / "labels_fullday.parquet"
    if labels.exists():
        con.execute(f"create table research_labels as select * from '{labels}'")
    for t in ("risk", "swaps", "sandwiches", "victims", "flagged", "research_labels"):
        n = con.execute(f"select count(*) from duckdb_tables() where table_name='{t}'").fetchone()[0]
        print(t, con.execute(f"select count(*) from {t}").fetchone()[0] if n else "absent")
    print(f"q90 gain {q90 / 1e9:.4f} SOL -> {out}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-run", default="live_20261014_184148")
    consolidate(ap.parse_args().since_run)
