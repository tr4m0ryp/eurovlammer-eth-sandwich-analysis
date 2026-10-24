"""Finetune report CLI.

Usage:
    python3 . report --n 1500 --seeds 20 --db data/live_.../live.duckdb
"""
from __future__ import annotations

import argparse

from ..locate import resolve_db
from .pipeline import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--db", type=str, default=None)
    a = ap.parse_args()
    main(a.n, a.seeds, resolve_db(a.db))
