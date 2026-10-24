"""Regenerate the README figures (light + dark) into ``docs/research/figures/readme/``.

Measured figures read the local run databases; the exposure curve evaluates the model itself on a
stated example victim, so it illustrates the mechanism rather than reporting a measurement.

Usage:
    python3 -m scripts.figures.readme
"""
from __future__ import annotations

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eth_mev.config import DATA_DIR, REPO  # noqa: E402
from eth_mev.measure.exposure.v2.mesp import _profit_buy, exposure, tokens_for_net  # noqa: E402
from eth_mev.onchain.decode.v2.swaps import Swap  # noqa: E402

OUT = REPO / "docs" / "research" / "figures" / "readme"
THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "grid": "#d0d7de", "a": "#0969da", "b": "#cf222e"},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "grid": "#30363d", "a": "#4493f8", "b": "#f85149"},
}
# V3 decode funnel, 146-swap mainnet window (docs/research/findings/strict-filter-analysis.md).
FUNNEL = [("V3 swaps decoded", 145), ("guard decodable", 6), ("guard > 0", 3),
          ("MESP > fees", 2), ("strict target", 1)]


def _style(ax, fig, th: dict) -> None:
    fig.patch.set_facecolor(th["bg"])
    ax.set_facecolor(th["bg"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(th["grid"])
    ax.tick_params(colors=th["fg"])
    ax.xaxis.label.set_color(th["fg"])
    ax.yaxis.label.set_color(th["fg"])
    ax.title.set_color(th["fg"])
    ax.set_axisbelow(True)
    ax.grid(True, color=th["grid"], linewidth=0.6, alpha=0.7)


def guard_cdf(th: dict, name: str) -> None:
    con = duckdb.connect(str(DATA_DIR / "guard_estimator.duckdb"), read_only=True)
    slacks = sorted(r[0] for r in con.execute("select slack from guard_slacks").fetchall())
    con.close()
    n = len(slacks)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot([max(s, 1e-7) * 100 for s in slacks], [(i + 1) / n for i in range(n)], color=th["a"], lw=2)
    ax.axvline(0.1, color=th["b"], ls="--", lw=1.2)
    ax.set_xscale("log")
    ax.set_xlabel("guard slack: actual out / amountOutMinimum - 1 (%)")
    ax.set_ylabel("share of victims")
    ax.set_title(f"How much room victims leave ({n:,} decodable V3 swaps)")
    _style(ax, fig, th)
    fig.savefig(OUT / f"guard-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def funnel(th: dict, name: str) -> None:
    labels, counts = zip(*FUNNEL)
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.barh(labels[::-1], counts[::-1], color=[th["b"]] + [th["a"]] * (len(counts) - 1))
    for i, c in enumerate(counts[::-1]):
        ax.text(c + 1.5, i, f"{c}  ({100 * c / counts[0]:.1f}%)", va="center", color=th["fg"])
    ax.set_xlim(0, counts[0] * 1.25)
    ax.set_xlabel("swaps")
    ax.set_title("Where V3 swaps drop out of the screen")
    _style(ax, fig, th)
    fig.savefig(OUT / f"funnel-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def exposure_curve(th: dict, name: str) -> None:
    """Profit of a front-run D on an example victim: 100 ETH / 200k token pool, 5 ETH buy, 3% slack."""
    x, y, amount = 100e18, 200_000e18, 5e18
    r = 0.003
    out = y * (amount / (1 + r)) / (x + amount / (1 + r))
    victim = Swap(tx_hash="", block_number=0, tx_index=0, log_index=0, venue="", pool="", token0="",
                  token1="", user="", is_buy=True, amount_in=int(amount), amount_out=0, x_pre=x,
                  y_pre=y, fee_bps=30.0, kind="in", amount=int(amount), limit=int(out / 1.03))
    f = _profit_buy(victim, r, x, y)
    ds = [i * 0.02e18 for i in range(0, 251)]
    vals = [f(d) for d in ds]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot([d / 1e18 for d, (_, ok) in zip(ds, vals) if ok],
            [p / 1e18 for p, ok in vals if ok], color=th["a"], lw=2, label="feasible: victim limit holds")
    ax.plot([d / 1e18 for d, (_, ok) in zip(ds, vals) if not ok],
            [p / 1e18 for p, ok in vals if not ok], color=th["b"], lw=1.5, ls=":",
            label="infeasible: victim reverts")
    ax.axhline(0, color=th["grid"], lw=1)
    ax.set_xlabel("front-run size D (ETH)")
    ax.set_ylabel("attacker profit (ETH)")
    ax.set_title("Sandwich profit against front size (example victim)")
    leg = ax.legend(frameon=False)
    for t in leg.get_texts():
        t.set_color(th["fg"])
    _style(ax, fig, th)
    fig.savefig(OUT / f"exposure-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


# Daily-profit-vs-cap model illustration. Reproduces the linear result from
# docs/research/methodology/sandwich-methodology.md §12 on the documented representative victim
# (100-ETH buy, 10% guard slack, 30 bps, ~2,000-ETH pool): profit per sandwich scales ~linearly
# with the per-trade capital cap until the victim's guard binds. TARGETS_PER_DAY is a stated model
# assumption, not a measurement (the measured whale rate is "one per several minutes").
TARGETS_PER_DAY = 288  # one sandwichable whale every 5 minutes


def capital_daily(th: dict, name: str) -> None:
    x, y, amount = 2_000e18, 200_000e18, 100e18
    r = 0.003
    out = tokens_for_net(x, y, amount / (1 + r))
    victim = Swap(tx_hash="", block_number=0, tx_index=0, log_index=0, venue="", pool="", token0="",
                  token1="", user="", is_buy=True, amount_in=int(amount), amount_out=0, x_pre=x,
                  y_pre=y, fee_bps=30.0, kind="in", amount=int(amount), limit=int(out / 1.10))
    caps = [i * 0.5e18 for i in range(1, 41)]  # 0.5 .. 20 ETH
    daily = [TARGETS_PER_DAY * exposure(victim, capital=c).mesp / 1e18 for c in caps]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot([c / 1e18 for c in caps], daily, color=th["a"], lw=2)
    ten = exposure(victim, capital=10e18)
    y10 = TARGETS_PER_DAY * ten.mesp / 1e18
    ax.plot([10.0], [y10], "o", color=th["b"], ms=7)
    ax.annotate(f"10 ETH cap\n≈ {ten.mesp / 1e18:.2f} ETH/trade, ≈ {y10:,.0f} ETH/day",
                xy=(10.0, y10), xytext=(10.6, 0.55 * y10), color=th["fg"], fontsize=10)
    ax.set_xlabel("max capital per trade (ETH)")
    ax.set_ylabel(f"expected daily profit (ETH, {TARGETS_PER_DAY:,} targets/day)")
    ax.set_title("Daily profit scales linearly with the per-trade capital cap")
    _style(ax, fig, th)
    fig.savefig(OUT / f"capital-daily-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, th in THEMES.items():
        guard_cdf(th, name)
        funnel(th, name)
        exposure_curve(th, name)
        capital_daily(th, name)
    print(f"wrote figures to {OUT}")


if __name__ == "__main__":
    main()
