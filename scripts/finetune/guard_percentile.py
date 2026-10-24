"""Sweep the guard-slack percentile: conservative screening vs tail-hunting.

Loads the persisted guard-slack distribution (DuckDB) and reports, for each candidate percentile,
(a) the default slack it implies and (b) the "over-estimation rate" — the fraction of victims whose
true room is *below* the assumed slack, i.e. the revert-risk of using that percentile as the default
for undecodable swaps. Lower percentiles = conservative (few false over-estimates, miss the tail);
higher = tail-hunting (catch high-room victims, risk reverting the low-room majority).

Usage:
    python3 . guard [--db data/guard_estimator.duckdb]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eth_mev.config import DATA_DIR

DEFAULT_DB = DATA_DIR / "guard_estimator.duckdb"


def load_slacks(db_path: Path) -> list[float]:
    import duckdb
    if not db_path.exists():
        print(f"no persisted guard data at {db_path}", file=sys.stderr)
        return []
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute("select slack from guard_slacks order by seen_ms").fetchall()
    except Exception as e:
        print(f"read error: {e}", file=sys.stderr)
        return []
    finally:
        con.close()
    return [float(r[0]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=str(DEFAULT_DB))
    a = ap.parse_args()

    slacks = load_slacks(Path(a.db))
    if not slacks:
        sys.exit(1)
    slacks.sort()
    n = len(slacks)
    print(f"persisted guard slacks: n = {n}\n")

    print(f"{'percentile':>11} {'default slack':>14} {'over-estimate rate (revert risk)':>33}")
    print("-" * 60)
    for p in (10, 25, 50, 75, 90, 95):
        k = min(n - 1, int(n * p / 100))
        slack = slacks[k]
        # over-estimation rate = P(true slack < assumed slack) ≈ p/100 (the percentile itself)
        print(f"{p:>10}% {slack*100:>13.4f}% {p:>30}%")

    print()
    med = slacks[n // 2]
    p90 = slacks[int(n * 0.90)]
    print(f"median (p50) slack = {med*100:.4f}%")
    print(f"p90 slack            = {p90*100:.4f}%")
    print()
    if p90 <= med * 1.5:
        print("Recommendation: the distribution is TIGHT — use a lower percentile (p25/p50) for a "
              "conservative screen; the tail is not much looser than the median.")
    else:
        print(f"Recommendation: the tail is materially looser (p90 {p90*100:.2f}% vs median "
              f"{med*100:.2f}%). For tail-hunting set MEV_GUARD_PERCENTILE=90; for a conservative "
              "screen set 25–50.")


if __name__ == "__main__":
    main()
