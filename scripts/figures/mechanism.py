"""Mechanism figures drawn from the README's own formulas (no measurement claims).

The sandwich price path uses a recorded pool's depth; the V2/V3 comparison is pure formula.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .measured import OUT, _con, _frame  # noqa: E402


def _buy(r_in: float, r_out: float, a: float, fee: float = 0.003) -> tuple[float, float, float]:
    """Constant-product swap: returns (r_in', r_out', out)."""
    eff = a * (1 - fee)
    out = r_out * eff / (r_in + eff)
    return r_in + a, r_out - out, out


def sandwich_path(th: dict, name: str) -> None:
    """Pool price through front, victim and back on the p90-impact recorded victim."""
    rows = _con().execute(
        "select x_pre, y_pre, amount_in / x_pre from included_swaps "
        "where x_pre > 0 and y_pre > 0 and amount_in / x_pre between 1e-6 and 1 "
        "order by 3").fetchall()
    x0, y0, imp = rows[int(0.9 * len(rows))]
    victim = imp * x0
    front = victim
    x1, y1, got = _buy(x0, y0, front)
    x2, y2, _ = _buy(x1, y1, victim)
    xb, yb, _ = _buy(y2, x2, got)  # sell the front's tokens back into the pool (reverse leg)
    price = lambda x, y: y / x  # noqa: E731
    labels = ["start", "after front", "after victim", "after back"]
    px = [price(x0, y0), price(x1, y1), price(x2, y2), price(yb, xb)]
    rel = [(p / px[0] - 1) * 1e4 for p in px]
    fig, ax = _frame(th)
    ax.plot(range(4), rel, color=th["a"], lw=2, marker="o")
    for i, r in enumerate(rel):
        ax.annotate(f"{r:+.1f} bp", (i, r), textcoords="offset points", xytext=(0, 8),
                    ha="center", color=th["fg"], fontsize=9)
    ax.set_xticks(range(4), labels)
    ax.set_ylabel("pool price vs start (bp)")
    ax.set_title("Illustration: one sandwich on a recorded pool, front = victim size", loc="left")
    fig.savefig(OUT / f"path-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def v3_vs_v2(th: dict, name: str) -> None:
    """Price impact of a buy sized as a share of capital, V2 against V3 ranges of equal capital."""
    shares = [10 ** (-4 + 3 * i / 200) for i in range(201)]
    fig, ax = _frame(th)
    ax.plot(shares, [(1 + 2 * s) ** 2 - 1 for s in shares],
            color=th["fg"], lw=2, label="V2, full range")
    for width, col in ((0.5, th["a"]), (0.1, th["b"]), (0.02, "#8250df")):
        pb, pa = 1 + width, 1 - width
        liq = 1 / (2 - 1 / pb ** 0.5 - pa ** 0.5)
        edge = liq * (pb ** 0.5 - 1)
        xs = [s for s in shares if s <= edge]
        ax.plot(xs, [(1 + s / liq) ** 2 - 1 for s in xs], color=col, lw=2, label=f"V3 range ±{width:.0%}")
        ax.axvline(edge, color=col, ls=":", lw=1)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("buy size as a share of the position's capital")
    ax.set_ylabel("price impact")
    ax.legend(frameon=False, labelcolor=th["fg"], fontsize=8)
    ax.set_title("Same capital, thinner range: more impact. Dotted: range edge (model stops)",
                 loc="left", fontsize=10)
    fig.savefig(OUT / f"v3range-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)
