"""One run directory: ``swaps/<block>.parquet`` holds every decoded swap, ``live.duckdb`` holds the
pending/included swaps, their settlement status, the sandwiches and the risk rows.

Follows ``tlive/store/writer.py``'s insert-only + settle-later pattern and ``consolidate.py``'s
dedup: swaps are appended (never deleted), settlement status is an UPDATE on a stable primary key,
and re-runs ``insert or ignore`` so data is never lost (compress/archive instead).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import pandas as pd

FLUSH_BLOCKS = 100

SCHEMA = """
create table if not exists pending_swaps (tx_hash varchar, block bigint, tx_index int, log_index int,
  pool varchar, venue varchar, token0 varchar, token1 varchar, user varchar, is_buy boolean,
  amount_in double, amount_out double, x_pre double, y_pre double, fee_bps double,
  kind varchar, amount double, "limit" double, deadline bigint, priority_fee double,
  base_fee double, p double, slack double, mesp double, seen_ms bigint,
  status varchar, attacker varchar, sw_profit double,
  primary key (tx_hash, log_index));
create table if not exists included_swaps (tx_hash varchar, block_number bigint, tx_index int, log_index int,
  pool varchar, venue varchar, token0 varchar, token1 varchar, user varchar, is_buy boolean,
  amount_in double, amount_out double, x_pre double, y_pre double, fee_bps double,
  kind varchar, amount double, "limit" double, deadline bigint,
  primary key (tx_hash, log_index));
create table if not exists victims (key varchar, tx_hash varchar, log_index int, block_number bigint,
  pool varchar, is_buy boolean, p double, status varchar, attacker varchar, sw_profit double);
create table if not exists sandwiches (key varchar primary key, block_number bigint, pool varchar,
  venue varchar, attacker varchar, profit double, n_victims int, seen_ms bigint);
create table if not exists risk (tx_hash varchar, log_index int, block_number bigint, pool varchar,
  venue varchar, token0 varchar, token1 varchar, is_buy boolean, position_wei numeric,
  x_ref double, mesp double, roi double, p double, priority_fee numeric, base_fee numeric,
  status varchar, attacker varchar, sw_profit double, primary key (tx_hash, log_index));
"""

PENDING_COLS = ["tx_hash", "block", "tx_index", "log_index", "pool", "venue", "token0", "token1",
                "user", "is_buy", "amount_in", "amount_out", "x_pre", "y_pre", "fee_bps", "kind",
                "amount", "limit", "deadline", "priority_fee", "base_fee", "p", "slack", "mesp", "seen_ms"]
INCLUDED_COLS = ["tx_hash", "block_number", "tx_index", "log_index", "pool", "venue", "token0",
                 "token1", "user", "is_buy", "amount_in", "amount_out", "x_pre", "y_pre", "fee_bps",
                 "kind", "amount", "limit", "deadline"]


class Store:
    def __init__(self, run_dir: Path, meta: dict):
        self.dir = run_dir
        (run_dir / "swaps").mkdir(parents=True, exist_ok=True)
        (run_dir / "risk").mkdir(parents=True, exist_ok=True)
        self.risk_buf: list[dict] = []
        (run_dir / f"manifest_{int(time.time())}.json").write_text(json.dumps(meta, indent=1))
        self.db = duckdb.connect(str(run_dir / "live.duckdb"),
                                 config={"memory_limit": "1GB", "threads": 2})
        self.db.execute(SCHEMA)
        self.buf: list[pd.DataFrame] = []
        self.buf_block0: int | None = None

    def swaps(self, df: pd.DataFrame, block: int) -> None:
        if df.empty:
            return
        if self.buf_block0 is None:
            self.buf_block0 = block
        df = df[[c for c in INCLUDED_COLS if c in df.columns]].copy()
        # Swap amounts are uint256 wei/token units that can exceed int64 (high-decimal tokens);
        # store them as float64 so pyarrow/parquet never overflow on a large amount.
        for c in ("amount_in", "amount_out", "x_pre", "y_pre"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        self.buf.append(df)
        if block - self.buf_block0 >= FLUSH_BLOCKS:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            pd.concat(self.buf, ignore_index=True).to_parquet(
                self.dir / "swaps" / f"{self.buf_block0}.parquet", index=False)
        self.buf, self.buf_block0 = [], None

    def pending(self, rows: list[dict]) -> None:
        """Insert-or-ignore pending swaps (insert-only, dedup on primary key)."""
        if rows:
            self.db.executemany(
                "insert or ignore into pending_swaps values (" + ",".join("?" * len(PENDING_COLS)) + ")",
                [[r.get(k) for k in PENDING_COLS] for r in rows])

    def included(self, rows: list[dict]) -> None:
        if rows:
            self.db.executemany(
                "insert or ignore into included_swaps values (" + ",".join("?" * len(INCLUDED_COLS)) + ")",
                [[r.get(k) for k in INCLUDED_COLS] for r in rows])

    def risk(self, rows: list[dict]) -> None:
        self.risk_buf += rows
        if len(self.risk_buf) >= 2000:
            self.flush_risk()
        if rows:
            cols = ["tx_hash", "log_index", "block_number", "pool", "venue", "token0", "token1",
                    "is_buy", "position_wei", "x_ref", "mesp", "roi", "p", "priority_fee", "base_fee",
                    "status", "attacker", "sw_profit"]
            self.db.executemany(
                "insert or ignore into risk values (" + ",".join("?" * len(cols)) + ")",
                [[r.get(k) for k in cols] for r in rows])

    def flush_risk(self) -> None:
        if self.risk_buf:
            cols = ["tx_hash", "log_index", "block_number", "pool", "venue", "token0", "token1",
                    "is_buy", "position_wei", "x_ref", "mesp", "roi", "p", "priority_fee", "base_fee",
                    "status", "attacker", "sw_profit"]
            df = pd.DataFrame([{k: r.get(k) for k in cols} for r in self.risk_buf])
            if not df.empty:
                df.to_parquet(self.dir / "risk" / f"{int(df.block_number.min())}.parquet", index=False)
        self.risk_buf = []

    def settle(self, block: int, settlement: dict) -> None:
        """Mark pending swaps at ``block``: sandwiched (attacker + profit) or clean."""
        for (block_no, tx_hash), (key, attacker, profit, *_) in settlement.items():
            if block_no != block:
                continue
            self.db.execute(
                "update pending_swaps set status='sandwiched', attacker=?, sw_profit=? "
                "where tx_hash=? ", [attacker, profit, tx_hash])
        self.db.execute(
            "update pending_swaps set status='clean' where block<=? and (status is null or status='pending')",
            [block])

    def sandwich(self, row: list, victims: list[list]) -> None:
        self.db.execute(
            "insert or ignore into sandwiches values (?,?,?,?,?,?,?,?)", row)
        self.db.executemany(
            "insert into victims values (?,?,?,?,?,?,?,?,?,?)", victims)

    def close(self) -> None:
        self.flush()
        self.flush_risk()
        self.db.close()
