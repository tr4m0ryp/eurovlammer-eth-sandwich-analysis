"""Finetune pipeline: train/holdout search, resolution sensitivity, and the markdown report.

Search runs on the first 80% of blocks; the last 20% is held out. The fast search uses the
deterministic *expected* P&L, then the top candidates are re-scored with a Monte-Carlo mean over
seeds. Produces ``notes/finetune-report.md`` with a go/no-go.

"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from .. import sweep
from ..backtest import build_replay, load_strict, run, run_expected, split_replay
from ..locate import OUT
from ..sweep import REFERENCE
from .render import REPORT, write_report

WIN_RATES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
CAPTURES = {"size-dependent (0.19/0.6)": None, "0.19": 0.19, "0.25": 0.25, "0.40": 0.40, "0.60": 0.60}
BRIBES = [int(0.005 * 1e18), int(0.01 * 1e18), int(0.02 * 1e18), int(0.05 * 1e18)]


def default_vector() -> dict:
    return {"MIN_P": 0.1, "MIN_PROFIT_WEI": 10_000_000, "THETA0": 0.012, "THETA_INF": 0.004,
            "BETA": 0.004, "ALPHA_SCARCE": 0.5, "MAX_SLIPPAGE_FRAC": 0.05,
            "MIN_COUNTERPARTY_P": 0.02, "MAX_PRIORITY_FEE_WEI": 50_000_000_000}


def mc(replay: dict, params: dict, seeds: int = 20) -> dict:
    rows = [run(replay, params, seed=s) for s in range(seeds)]
    out = {"commits": statistics.mean(r["commits"] for r in rows),
           "wins": statistics.mean(r["wins"] for r in rows),
           "outbid": statistics.mean(r["outbid"] for r in rows),
           "no_sandwich": statistics.mean(r["no_sandwich"] for r in rows),
           "sandwiched": statistics.mean(r["sandwiched"] for r in rows),
           "max_drawdown": statistics.mean(r["max_drawdown"] for r in rows)}
    pr = [r["total_profit_eth"] for r in rows]
    out["total_profit_eth"] = statistics.mean(pr)
    out["profit_std"] = statistics.pstdev(pr) if len(pr) > 1 else 0.0
    out["profit_min"] = min(pr)
    out["profit_max"] = max(pr)
    out["sharpe"] = statistics.mean(r["sharpe"] for r in rows)
    return out


def _leverage(replay_train, best) -> dict:
    base = run_expected(replay_train, dict(REFERENCE, **best))["total_profit_eth"]
    out = {}
    for k in sorted(sweep.RANGES):
        lo, hi = sweep.RANGES[k]
        grid = [int(lo), int((lo * hi) ** 0.5), int(hi)] if k in sweep.LOG_UNIFORM else [lo, (lo + hi) / 2, hi]
        devs = [abs(run_expected(replay_train, dict(REFERENCE, **dict(best, **{k: v})))["total_profit_eth"] - base)
                for v in grid]
        out[k] = max(devs)
    return out


def main(n: int, seeds: int, db_path: Path) -> None:
    cache = db_path.parent / "strict_replay.parquet"
    df = load_strict(db_path, cache)
    if df.empty:
        print("no strict events in the DuckDB; cannot finetune. Collect a ground-truth window first.",
              file=sys.stderr)
        sys.exit(2)
    replay = build_replay(df)
    train, hold = split_replay(replay, frac=0.8)
    print(f"train {train['n']} rows / holdout {hold['n']} rows")

    OUT.mkdir(parents=True, exist_ok=True)
    results = sweep.search(train, n, rng_seed=1)
    refined = []
    for r in results[:6]:
        refined.append(sweep.refine(train, r["params"]))
    candidates = [r["params"] for r in refined] + [r["params"] for r in results[:8]]
    seen, uniq = set(), []
    for c in candidates:
        k = json.dumps(c, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    scored = []
    for c in uniq:
        m = mc(train, c, seeds)
        if m["commits"] >= sweep.FEASIBLE_COMMITS and m["max_drawdown"] <= sweep.FEASIBLE_DD:
            scored.append((m["total_profit_eth"], c, m))
    scored.sort(key=lambda t: -t[0])
    best = scored[0][1] if scored else uniq[0]
    best_m = {"params": best, "metrics": mc(train, best, seeds)}
    (OUT / "best_train.json").write_text(json.dumps(best_m, indent=2, default=float))

    table = [{"label": "default (train)", **mc(train, default_vector(), seeds)}]
    for i, (_, c, m) in enumerate(scored[:4], 1):
        table.append({"label": f"candidate {i} (train)", **m})
    table.append({"label": "best \u2192 holdout", **mc(hold, best, seeds)})

    pareto = [{"MIN_P": mp, **mc(train, dict(default_vector(), MIN_P=mp), seeds)}
              for mp in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4)]
    wr_curve = [{"WIN_RATE": wr, **mc(train, dict(REFERENCE, **best, WIN_RATE=wr), seeds)} for wr in WIN_RATES]
    cap_curve = [{"capture": label, **mc(train, dict(REFERENCE, **best, CAPTURE_FRACTION=c), seeds)}
                 for label, c in CAPTURES.items()]
    bribe_curve = [{"BRIBE_WEI": t, **mc(train, dict(REFERENCE, **best, BRIBE_WEI=t), seeds)} for t in BRIBES]
    full_best = mc(replay, dict(REFERENCE, **best), seeds)
    leverage_agg = _leverage(train, best)

    (OUT / "report.json").write_text(json.dumps(
        {"best": best_m, "table": table, "wr_curve": wr_curve, "cap_curve": cap_curve,
         "bribe_curve": bribe_curve, "full_best": full_best, "leverage": leverage_agg,
         "pareto": pareto}, indent=2, default=float))

    n_sandwiched = int(df.sandwiched.sum()) if "sandwiched" in df else 0
    write_report(best, best_m["metrics"], table, wr_curve, cap_curve, bribe_curve,
                 full_best, leverage_agg, pareto, seeds, len(df), n_sandwiched)
    print(f"wrote {REPORT}")
