"""Replay backtest CLI.

Usage:
    python3 . backtest --cache --db data/live_.../live.duckdb
    python3 . backtest --vector '{"MIN_P":0.1}' --db ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pprint import pprint

from ..locate import resolve_db
from .replay import build_replay, load_strict, reconstruct
from .simulate import run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--vector", type=str, default="{}")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--db", type=str, default=None)
    args = ap.parse_args()
    db = resolve_db(args.db)
    cache = db.parent / "strict_replay.parquet"
    if args.cache:
        reconstruct(db, cache)
        sys.exit(0)
    df = load_strict(db, cache)
    replay = build_replay(df)
    params = json.loads(args.vector)
    pprint(run(replay, params, seed=args.seed))


if __name__ == "__main__":
    main()
