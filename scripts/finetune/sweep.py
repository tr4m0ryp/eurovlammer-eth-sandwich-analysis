"""Parameter sweep + refinement for the capital-allocation engine (read-only, offline replay).

Searches the *selection* knobs (which targets to commit and how large) at a reference *resolution*
(WIN_RATE / CAPTURE_FRACTION / TIP_LAMPORTS), then reports the resolution sensitivity analytically.

    .venv/bin/python -m scripts.finetune.sweep --n 1500            # random search
    .venv/bin/python -m scripts.finetune.sweep --refine            # coordinate descent on the best
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import build_replay, load_strict, run, run_expected, run_mc  # noqa: E402

MC_SEEDS = 5                      # seeds per evaluation during the fast search/refine

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "exports" / "finetune"

# Ranges (prompt suggestions).
RANGES = {
    "MIN_P": (0.02, 0.4),
    "MIN_PI_LAMPORTS": (100_000, 5_000_000),          # log-uniform
    "THETA0": (0.004, 0.03),
    "THETA_INF": (0.001, 0.012),
    "BETA": (0.0, 0.02),
    "ALPHA_SCARCE": (0.25, 1.0),
    "MAX_SLIPPAGE_FRAC": (0.02, 0.2),
    "MIN_COUNTERPARTY_P": (0.0, 0.1),
    "MAX_PRIORITY_FEE": (100_000, 5_000_000),         # log-uniform
}
LOG_UNIFORM = {"MIN_PI_LAMPORTS", "MAX_PRIORITY_FEE"}

# Reference resolution settings (the current live defaults).
REFERENCE = {"WIN_RATE": 0.5, "TIP_LAMPORTS": 100_000, "CAPTURE_FRACTION": None}  # None = size-dependent

FEASIBLE_COMMITS = 50
FEASIBLE_DD = 0.25


def sample(rng: random.Random) -> dict:
    p = {}
    for k, (lo, hi) in RANGES.items():
        if k in LOG_UNIFORM:
            p[k] = float(math.exp(rng.uniform(math.log(lo), math.log(hi))))
            p[k] = int(round(p[k]))
        else:
            p[k] = rng.uniform(lo, hi)
    return p


def feasible(m: dict) -> bool:
    return m["commits"] >= FEASIBLE_COMMITS and m["max_drawdown"] <= FEASIBLE_DD


def score(m: dict) -> tuple:
    """Ranking tuple: feasibility first, then expected profit, then Sharpe."""
    return (feasible(m), m["total_profit_sol"], m.get("sharpe", 0.0))


def evaluate(replay: dict, params: dict, seed: int = 0) -> dict:
    """Monte-Carlo mean P&L across the win/race draws: the honest (unbiased) objective."""
    m = run_mc(replay, dict(REFERENCE, **params), seeds=MC_SEEDS)
    m["params"] = params
    return m


def search(replay: dict, n: int, rng_seed: int = 1) -> list[dict]:
    rng = random.Random(rng_seed)
    results = []
    for _ in range(n):
        results.append(evaluate(replay, sample(rng)))
    results.sort(key=score, reverse=True)
    return results


def refine(replay: dict, start: dict, steps: int = 8, rng_seed: int = 7) -> dict:
    """Coordinate descent: vary one knob at a time, keep moves that improve the score."""
    best_p = dict(start)
    best_m = evaluate(replay, best_p)
    for _ in range(steps):
        improved = False
        for k, (lo, hi) in RANGES.items():
            sign = 1 if k in LOG_UNIFORM else 1
            # multiplicative step for log-uniform, additive for linear
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


def hist(results: list[dict]) -> None:
    pr = [r["total_profit_sol"] for r in results]
    pr.sort()
    n = len(pr)
    feas = [r for r in results if feasible(r)]
    print(f"profit distribution (n={n}): min {pr[0]:.4f}  p10 {pr[n//10]:.4f}  median {pr[n//2]:.4f}  "
          f"p90 {pr[9*n//10]:.4f}  max {pr[-1]:.4f}")
    print(f"positive: {sum(1 for x in pr if x > 0)}/{n}   "
          f"feasible (commits>={FEASIBLE_COMMITS}, dd<={FEASIBLE_DD}): {len(feas)}/{n}")


def show(results: list[dict], top: int = 12) -> None:
    print(f"{'profit':>8} {'commits':>8} {'wins':>5} {'race':>5} {'no_cp':>6} {'hit':>5} {'dd':>6}  params")
    for r in results[:top]:
        hit = r["sandwiched"] / r["commits"] if r["commits"] else 0
        p = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["params"].items()}
        print(f"{r['total_profit_sol']:8.4f} {r['commits']:8d} {r['wins']:5.1f} {r['race_loss']:5.1f} "
              f"{r['no_cp']:6d} {hit:5.2f} {r['max_drawdown']:6.3f}  {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--refine-best", type=str, default=None, help="JSON params to refine")
    args = ap.parse_args()

    df = load_strict()
    replay = build_replay(df)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.refine_best:
        m = refine(replay, json.loads(args.refine_best))
        show([m], top=1)
        (OUT / "best.json").write_text(json.dumps(m, indent=2, default=float))
        sys.exit(0)

    results = search(replay, args.n, args.seed)
    hist(results)
    show(results, top=15)
    (OUT / f"random_{args.n}.json").write_text(json.dumps(results, indent=2, default=float))
    after = []
    for r in results[:6]:
        print("\nrefining:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["params"].items()})
        m = refine(replay, r["params"])
        after.append(m)
    after.sort(key=score, reverse=True)
    print("\n=== refined ===")
    show(after, top=6)
    (OUT / "refined.json").write_text(json.dumps(after, indent=2, default=float))
    (OUT / "best.json").write_text(json.dumps(after[0], indent=2, default=float))