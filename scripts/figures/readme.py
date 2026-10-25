"""Regenerate the README figures (light + dark) into ``docs/research/figures/readme/``.

Measured figures read the local run databases; the exposure curve evaluates the model itself on a
stated example victim, so it illustrates the mechanism rather than reporting a measurement.

Usage:
    python3 -m scripts.figures.readme
"""
from __future__ import annotations

import random

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eth_mev.config import DATA_DIR, REPO  # noqa: E402
from eth_mev.measure.exposure.v2.mesp import _profit_buy, tokens_for_net  # noqa: E402
from eth_mev.onchain.decode.v2.swaps import Swap  # noqa: E402

from .measured import measured_figures  # noqa: E402
from .mechanism import sandwich_path, v3_vs_v2  # noqa: E402
from .visibility import guard_ecdf, visibility  # noqa: E402

OUT = REPO / "docs" / "research" / "figures" / "readme"
THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "grid": "#d0d7de", "a": "#0969da", "b": "#cf222e"},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "grid": "#30363d", "a": "#4493f8", "b": "#f85149"},
}


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


# Realistic daily-EV-vs-front curve, computed from recorded data: the victim price-impact (ROI)
# distribution read off the ground-truth swaps, and the guard-slack distribution off the guard
# estimator. MESP at a front C is min(C, x*slack) * roi, because the return per sandwich is the
# victim's own price impact and the slack only caps how big a front the victim's guard absorbs.
# The curve rises to ~10 ETH then saturates: most victims cannot absorb a larger front.
POOL_DEPTH_ETH = 500.0   # representative liquid pool depth (ETH), stated assumption
COST = 0.0003            # bribe + gas, ETH
WRCAP = 0.125            # WIN_RATE 0.5 x CAPTURE_FRACTION 0.25
N_DAY = 22_000.0         # measured decodable-victim rate (~2,740 over the 3h window)
N_SAMPLES = 4_000


def _load_rois() -> list[float]:
    """Victim price impact (amount_in / x_pre) from the newest ground-truth run."""
    dbs = sorted(DATA_DIR.glob("gt_*/live.duckdb"))
    if not dbs:
        raise SystemExit("no ground-truth run found for the ROI distribution")
    con = duckdb.connect(str(dbs[-1]), read_only=True)
    rows = con.execute(
        "select amount_in, x_pre from included_swaps "
        "where amount_in is not null and x_pre is not null and x_pre > 0").fetchall()
    con.close()
    return [a / x for a, x in rows if 0.0 < a / x < 1.0]


def _load_slacks() -> list[float]:
    con = duckdb.connect(str(DATA_DIR / "guard_estimator.duckdb"), read_only=True)
    rows = [r[0] for r in con.execute("select slack from guard_slacks").fetchall()]
    con.close()
    return [s for s in rows if s <= 1.0]


def capital_daily(th: dict, name: str) -> None:
    rois = _load_rois()
    slacks = _load_slacks()
    rng = random.Random(0)
    roi_s = rng.choices(rois, k=N_SAMPLES)
    slack_s = rng.choices(slacks, k=N_SAMPLES)
    caps = [i * 0.5 for i in range(1, 101)]  # 0.5 .. 50 ETH
    daily = []
    for c in caps:
        tot = 0.0
        for roi, sl in zip(roi_s, slack_s):
            g = WRCAP * min(c, POOL_DEPTH_ETH * sl) * roi - COST
            if g > 0:
                tot += g
        daily.append(N_DAY * tot / N_SAMPLES)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(caps, daily, color=th["a"], lw=2)
    y10 = daily[caps.index(10.0)]
    ax.plot([10.0], [y10], "o", color=th["b"], ms=7)
    ax.annotate(f"10 ETH front → ≈ {y10:.0f} ETH/day", xy=(10.0, y10),
                xytext=(14, y10 - 5), color=th["fg"], fontsize=10)
    ax.annotate("own capital 0.09 ETH → ≈ 0.04 ETH/day", xy=(0.09, 0.0),
                xytext=(4.0, daily[0] + 5), color=th["b"], fontsize=9)
    ax.set_xlim(0, 50)
    ax.set_ylim(0, max(daily) * 1.15)
    ax.set_xlabel("front capital (ETH)")
    ax.set_ylabel("expected daily profit (ETH)")
    ax.set_title("Realistic daily EV saturates past ~10 ETH of front")
    _style(ax, fig, th)
    fig.savefig(OUT / f"capital-daily-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, th in THEMES.items():
        guard_ecdf(th, name)
        visibility(th, name)
        sandwich_path(th, name)
        v3_vs_v2(th, name)
        exposure_curve(th, name)
        capital_daily(th, name)
        measured_figures(th, name)
    print(f"wrote figures to {OUT}")


if __name__ == "__main__":
    main()
