"""Finetune pipeline: train/holdout search, resolution sensitivity, and the markdown report.

Search runs on the first 80% of blocks; the last 20% is held out. The fast search uses the
deterministic *expected* P&L, then the top candidates are re-scored with a Monte-Carlo mean over
seeds. Produces ``notes/finetune-report.md`` with a go/no-go.

       .venv/bin/python -m scripts.finetune.report --n 1500 --seeds 20 --db data/live_.../live.duckdb
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweep  # noqa: E402
from backtest import (build_replay, load_strict, run, run_expected, split_replay)  # noqa: E402
from sweep import REFERENCE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "exports" / "finetune"
REPORT = ROOT / "notes" / "finetune-report.md"

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


def fmt_vec(b: dict) -> dict:
    return {k: (round(v, 3) if isinstance(v, float) else (int(round(v)) if k in sweep.LOG_UNIFORM else v))
            for k, v in b.items()}


def write_report(best, best_mc, table, wr_curve, cap_curve, bribe_curve, full_best,
                 leverage_agg, pareto, seeds, n_rows, n_sandwiched):
    b = fmt_vec(best)
    env_rows = "| Symbol | Env |\n|---|---|\n" + "".join(
        f"| `{k}` | `MEV_{k}` = `{v}` |\n" for k, v in b.items() if k in
        ("MIN_P", "MIN_PROFIT_WEI", "MAX_SLIPPAGE_FRAC", "MIN_COUNTERPARTY_P", "MAX_PRIORITY_FEE_WEI"))
    L = []
    L.append("# Finetune report: Ethereum mempool-sandwich engine\n")
    L.append("Backtest replays the persisted strict event stream (rebuilt offline: `interesting` → "
             "budget-capped V2 exposure → ROI≥1%) through the live `Allocator` plus the three tunable "
             "opportunity-risk gates, settled against the run's real inclusion-order settlement. "
             "Selection knobs are tuned on the first 80% of blocks; the last 20% is held out. Numbers "
             f"are the mean over {seeds} seeds ± std, because realised profit is dominated by a few rare "
             "large-MESP (`whale`) targets whose commitment is path-dependent.\n")
    L.append(f"\nData window: {n_rows} strict events, of which {n_sandwiched} were actually sandwiched "
             f"({100*n_sandwiched/max(n_rows,1):.1f}%).\n")
    L.append("\n## 1. Optimal parameter vector (selection knobs)\n")
    for k in sorted(b):
        L.append(f"- `{k}` = `{b[k]}`")
    L.append("\nEnv-var equivalents:\n\n" + env_rows)
    L.append("\n## 2. Backtest table (mean over seeds @ WR=0.5 / capture size-dep / bribe 0.01 ETH)\n")
    L.append("| candidate | commits | wins | outbid | no_sw | profit (ETH) | profit ± | max DD | Sharpe |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in table:
        L.append(f"| {r['label']} | {r['commits']:.0f} | {r['wins']:.0f} | {r['outbid']:.0f} | "
                 f"{r['no_sandwich']:.0f} | {r['total_profit_eth']:+.4f} | {r['profit_std']:.4f} | "
                 f"{r['max_drawdown']*100:.1f}% | {r['sharpe']:+.3f} |")
    L.append("\n## 2b. Throughput vs profit (Pareto): the MIN_P selectivity knob\n")
    L.append("| MIN_P | commits | profit (ETH) | profit ± |")
    L.append("|---|---|---|---|")
    for r in pareto:
        L.append(f"| {r['MIN_P']} | {r['commits']:.0f} | {r['total_profit_eth']:+.4f} | {r['profit_std']:.4f} |")
    L.append("\n## 3. Resolution sensitivity (WIN_RATE is unknowable offline)\n")
    L.append("| WIN_RATE | expected profit (ETH) | commits |")
    L.append("|---|---|---|")
    for r in wr_curve:
        L.append(f"| {r['WIN_RATE']:.1f} | {r['total_profit_eth']:+.4f} | {r['commits']:.0f} |")
    L.append("\n| CAPTURE_FRACTION | expected profit (ETH) |")
    L.append("|---|---|")
    for r in cap_curve:
        L.append(f"| {r['capture']} | {r['total_profit_eth']:+.4f} |")
    L.append("\n| BRIBE_WEI | expected profit (ETH) |")
    L.append("|---|---|")
    for r in bribe_curve:
        L.append(f"| {r['BRIBE_WEI']} | {r['total_profit_eth']:+.4f} |")
    L.append(f"\nBest selection full-window: **{full_best['total_profit_eth']:+.4f} ± "
             f"{full_best['profit_std']:.4f} ETH** over {full_best['commits']:.0f} commits "
             f"({full_best['sandwiched']:.0f} sandwiched).\n")
    L.append("\n## 4. What moves the needle\n")
    L.append("| knob | max |Δ expected profit| (ETH) |")
    L.append("|---|---|")
    for k, d in sorted(leverage_agg.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {d:.4f} |")
    L.append("\n## 5. Recommendation + go/no-go\n\n")
    L.append(_recommendation(full_best))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")


def _recommendation(full_best) -> str:
    """The go/no-go. This is grounded in the Solana predecessor's measured outlook (backrun-strategy.md):
    no confirmed-block bot found a positive-EV sandwich/back-run/arb edge on real data, and the ETH
    public-mempool subset is further thinned by private order flow. The honest default is NO-GO unless
    the dry-run is clearly positive after fees with WIN_RATE measured (not assumed) on a live sample."""
    mean = full_best["total_profit_eth"]
    std = full_best["profit_std"]
    commits = full_best["commits"]
    if mean > 0 and std > 0 and mean > std and commits >= 50:
        verdict = ("**GO** is plausible *only if* WIN_RATE is subsequently measured \u2265~0.6 on a small "
                   "live sample and the after-fee mean stays positive. This offline run is necessary but "
                   "not sufficient.")
    else:
        verdict = ("**NO-GO by default.** The dry-run is not clearly positive after fees (base fee + "
                   "priority fee + bribe). WIN_RATE is unknowable offline and expected P&L is dominated by "
                   "a few rare whale targets; do not recommend going live until these are measured.")
    return (f"Expected dry-run P&L: **{mean:+.4f} ± {std:.4f} ETH** over {commits:.0f} commits. "
            f"{verdict}")


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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--db", type=str, default=None)
    a = ap.parse_args()
    db = Path(a.db) if a.db else next((ROOT / "data").glob("live_*/live.duckdb"), None)
    if db is None:
        print("no DuckDB found; pass --db", file=sys.stderr)
        sys.exit(1)
    main(a.n, a.seeds, db)
