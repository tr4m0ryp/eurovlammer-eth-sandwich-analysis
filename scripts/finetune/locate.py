"""Locate the run database a finetune step reads: ``--db`` if given, else the first ``data/live_*``."""
from __future__ import annotations

import sys
from pathlib import Path

from eth_mev.paths import DATA_DIR

OUT = DATA_DIR / "exports" / "finetune"


def resolve_db(arg: str | None) -> Path:
    """Return the DuckDB path, exiting with status 1 when none is given or found."""
    db = Path(arg) if arg else next(DATA_DIR.glob("live_*/live.duckdb"), None)
    if db is None:
        print("no DuckDB found; pass --db", file=sys.stderr)
        sys.exit(1)
    return db
