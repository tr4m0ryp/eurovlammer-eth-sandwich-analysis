"""Markdown rendering of the finetune report, including the go/no-go recommendation."""
from __future__ import annotations

from eth_mev.paths import ROOT

from .. import sweep

REPORT = ROOT / "notes" / "reports" / "finetune-report.md"


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
