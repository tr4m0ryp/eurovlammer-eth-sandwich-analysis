"""Parameter sweep + refinement for the capital-allocation engine (read-only, offline replay).

Searches the *selection* knobs at a reference *resolution* (WIN_RATE / CAPTURE_FRACTION / BRIBE_WEI),
then reports the resolution sensitivity analytically. Adapted from ``tlive/scripts/finetune/sweep.py``.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys

from .backtest import build_replay, load_strict, run_mc
from .locate import OUT, resolve_db

MC_SEEDS = 5


# Ranges (finetune-prompt.md suggestions, ETH).
RANGES = {
    "MIN_P": (0.02, 0.4),
    "MIN_PROFIT_WEI": (100_000, 5_000_000_000),       # log-uniform wei
    "THETA0": (0.004, 0.03),
    "THETA_INF": (0.001, 0.012),
    "BETA": (0.0, 0.02),
    "ALPHA_SCARCE": (0.25, 1.0),
    "MAX_SLIPPAGE_FRAC": (0.02, 0.2),
    "MIN_COUNTERPARTY_P": (0.0, 0.1),
    "MAX_PRIORITY_FEE_WEI": (1_000_000_000, 200_000_000_000),   # log-uniform wei
}
LOG_UNIFORM = {"MIN_PROFIT_WEI", "MAX_PRIORITY_FEE_WEI"}

REFERENCE = {"WIN_RATE": 0.5, "BRIBE_WEI": int(0.01 * 1e18), "CAPTURE_FRACTION": None}

FEASIBLE_COMMITS = 50
FEASIBLE_DD = 0.25


def sample(rng: random.Random) -> dict:
    p = {}
    for k, (lo, hi) in RANGES.items():
        if k in LOG_UNIFORM:
            v = math.exp(rng.uniform(math.log(lo), math.log(hi)))
            p[k] = int(round(v))
        else:
            p[k] = rng.uniform(lo, hi)
    return p


def feasible(m: dict) -> bool:
    return m["commits"] >= FEASIBLE_COMMITS and m["max_drawdown"] <= FEASIBLE_DD


def score(m: dict) -> tuple:
    return (feasible(m), m["total_profit_eth"], m.get("sharpe", 0.0))


def evaluate(replay: dict, params: dict) -> dict:
    m = run_mc(replay, dict(REFERENCE, **params), seeds=MC_SEEDS)
    m["params"] = params
    return m


def search(replay: dict, n: int, rng_seed: int = 1) -> list[dict]:
    rng = random.Random(rng_seed)
    results = [evaluate(replay, sample(rng)) for _ in range(n)]
    results.sort(key=score, reverse=True)
    return results


def refine(replay: dict, start: dict, steps: int = 8) -> dict:
    best_p = dict(start)
    best_m = evaluate(replay, best_p)
    for _ in range(steps):
        improved = False
        for k, (lo, hi) in RANGES.items():
            for d in (-1, 1):
                if k in LOG_UNIFORM:
                    v = best_p[k] * (1.3 if d > 0 else 1 / 1.3)
                    v = int(round(min(max(v, lo), hi)))
                else:
                    span = hi - lo
                    v = best_p[k] + d * span / 12
                    v = min(max(v, lo), hi)
                cand = dict(best_p, **{k: v})
                m = evaluate(replay, cand)
                if score(m) > score(best_m):
                    best_m, best_p, improved = m, cand, True
        if not improved:
            break
    return best_m


def show(results: list[dict], top: int = 12) -> None:
    print(f"{'profit':>8} {'commits':>8} {'wins':>5} {'outbid':>6} {'no_sw':>6} {'hit':>5} {'dd':>6}  params")
    for r in results[:top]:
        hit = r["sandwiched"] / r["commits"] if r["commits"] else 0
        p = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["params"].items()}
        print(f"{r['total_profit_eth']:8.4f} {r['commits']:8d} {r['wins']:5.1f} {r['outbid']:6.1f} "
              f"{r['no_sandwich']:6d} {hit:5.2f} {r['max_drawdown']:6.3f}  {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--refine-best", type=str, default=None)
    args = ap.parse_args()
    db = resolve_db(args.db)
    df = load_strict(db, db.parent / "strict_replay.parquet")
    replay = build_replay(df)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.refine_best:
        m = refine(replay, json.loads(args.refine_best))
        show([m], top=1)
        (OUT / "best.json").write_text(json.dumps(m, indent=2, default=float))
        sys.exit(0)
    results = search(replay, args.n)
    show(results, top=15)
    (OUT / f"random_{args.n}.json").write_text(json.dumps(results, indent=2, default=float))
