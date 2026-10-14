"""One run directory: ``swaps/<slot0>.parquet`` holds every scored swap (flushed every
``FLUSH_SLOTS``), ``live.duckdb`` holds the flagged swaps, their settled status and the sandwiches."""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import pandas as pd

FLUSH_SLOTS = 500
SCHEMA = """
create table if not exists flagged (signature varchar, ev_index int, slot bigint, block_time bigint,
  venue varchar, pool varchar, mint varchar, signer varchar, router varchar, is_buy boolean, sol bigint,
  x double, fee_bps double, slack double, mesp double, phi double, p double, cu_price bigint,
  jito_tip bigint, dontfront boolean, status varchar, attacker varchar, sw_profit double, seen_ms bigint,
  primary key (signature, ev_index));
create table if not exists sandwiches (key varchar primary key, slot bigint, pool varchar, venue varchar,
  attacker varchar, profit double, n_victims int, n_flagged int, victim_sol bigint, seen_ms bigint);
create table if not exists victims (key varchar, signature varchar, ev_index int, slot bigint, sol bigint,
  p double, flagged boolean);
"""
KEEP = ["signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
        "is_buy", "sol", "x_ref", "fee_bps", "slack", "mesp", "phi", "p", "cu_price", "jito_tip",
        "dontfront", "flag", "kind", "ix_name", "amount", "limit", "user", "tx_index", "x_pre", "y_pre",
        "tok", "log_flow_rel", "log_pool_n", "log_wallet_n", "priority_fee"]


class Store:
    def __init__(self, run_dir: Path, meta: dict):
        self.dir = run_dir
        (run_dir / "swaps").mkdir(parents=True, exist_ok=True)
        (run_dir / f"manifest_{int(time.time())}.json").write_text(json.dumps(meta, indent=1))
        self.db = duckdb.connect(str(run_dir / "live.duckdb"), config={"memory_limit": "1GB", "threads": 2})
        self.db.execute(SCHEMA)
        self.buf: list[pd.DataFrame] = []
        self.buf_slot0: int | None = None

    def swaps(self, df: pd.DataFrame, slot: int) -> None:
        if df.empty:
            return
        if self.buf_slot0 is None:
            self.buf_slot0 = slot
        self.buf.append(df[[c for c in KEEP if c in df.columns]])
        if slot - self.buf_slot0 >= FLUSH_SLOTS:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            pd.concat(self.buf, ignore_index=True).to_parquet(self.dir / "swaps" / f"{self.buf_slot0}.parquet", index=False)
        self.buf, self.buf_slot0 = [], None

    def flagged(self, rows: list[dict]) -> None:
        if rows:
            self.db.executemany("insert or ignore into flagged values (" + ",".join("?" * 24) + ")",
                                [[r[k] for k in FLAG_COLS] for r in rows])

    def settle(self, slot: int, victims: dict[tuple[str, int], tuple[str, str, float]]) -> None:
        """Mark flagged swaps at ``slot``: sandwiched (with attacker and profit) or clean."""
        for (sig, ev), (key, att, prof) in victims.items():
            self.db.execute("update flagged set status='sandwiched', attacker=?, sw_profit=? "
                            "where signature=? and ev_index=?", [att, prof, sig, ev])
        self.db.execute("update flagged set status='clean' where slot<=? and status='pending'", [slot])

    def sandwich(self, row: list, victims: list[list]) -> None:
        self.db.execute("insert or ignore into sandwiches values (?,?,?,?,?,?,?,?,?,?)", row)
        self.db.executemany("insert into victims values (?,?,?,?,?,?,?)", victims)

    def close(self) -> None:
        self.flush()
        self.db.close()


FLAG_COLS = ["signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
             "is_buy", "sol", "x_ref", "fee_bps", "slack", "mesp", "phi", "p", "cu_price", "jito_tip",
             "dontfront", "status", "attacker", "sw_profit", "seen_ms"]
