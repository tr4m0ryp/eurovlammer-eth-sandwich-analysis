"""Measured exposure figures from the recorded ground-truth run (light + dark).

Every number is read from ``data/gt_*/live.duckdb``; nothing is modelled. Run through
``python3 -m scripts.figures.readme``, which imports ``measured_figures``.
"""
from __future__ import annotations

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eth_mev.config import DATA_DIR, REPO  # noqa: E402

OUT = REPO / "docs" / "research" / "figures" / "readme"


def _con() -> duckdb.DuckDBPyConnection:
    dbs = sorted(DATA_DIR.glob("gt_*/live.duckdb"))
    if not dbs:
        raise SystemExit("no ground-truth run found")
    return duckdb.connect(str(dbs[-1]), read_only=True)


def _frame(th: dict, w: float = 7.0, h: float = 3.6):
    fig, ax = plt.subplots(figsize=(w, h), facecolor=th["bg"])
    ax.set_facecolor(th["bg"])
    for s in ax.spines.values():
        s.set_color(th["grid"])
    ax.tick_params(colors=th["fg"])
    ax.grid(color=th["grid"], lw=0.5, alpha=0.6)
    ax.xaxis.label.set_color(th["fg"])
    ax.yaxis.label.set_color(th["fg"])
    ax.title.set_color(th["fg"])
    return fig, ax


def price_impact(th: dict, name: str) -> None:
    """Share of swaps whose own price impact exceeds a threshold: what a guard must absorb."""
    con = _con()
    rows = con.execute("select amount_in / x_pre from included_swaps "
                       "where amount_in is not null and x_pre > 0").fetchall()
    xs = sorted(r[0] for r in rows if 0 < r[0] < 1)
    dust = sum(1 for x in xs if x < 1e-6) / len(xs)
    xs = [x for x in xs if x >= 1e-6]
    n = len(xs)
    fig, ax = _frame(th)
    ax.step(xs, [1 - i / n for i in range(n)], color=th["a"], lw=2)
    ax.set_xscale("log")
    ax.set_xlabel("victim price impact (swap size / input reserve)")
    ax.set_ylabel("share of swaps above")
    ax.set_title(f"Swaps above 1e-6 impact (n={n:,}); {dust:.0%} of all are dust below it", loc="left")
    fig.savefig(OUT / f"impact-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def pool_concentration(th: dict, name: str) -> None:
    """Cumulative swap share by pool rank: how concentrated the flow is."""
    con = _con()
    counts = [r[0] for r in con.execute(
        "select count(*) c from included_swaps group by pool order by c desc").fetchall()]
    total = sum(counts)
    cum, acc = [], 0
    for c in counts:
        acc += c
        cum.append(acc / total)
    fig, ax = _frame(th)
    ax.plot(range(1, len(cum) + 1), cum, color=th["a"], lw=2)
    ax.set_xlabel("pools ranked by swap count")
    ax.set_ylabel("cumulative share of swaps")
    ax.set_title(f"Flow is concentrated: {len(counts)} pools, {total:,} swaps", loc="left")
    fig.savefig(OUT / f"pools-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def block_flow(th: dict, name: str) -> None:
    """V2 swaps per block; blocks where one wallet trades the same pool twice are highlighted."""
    con = _con()
    rows = con.execute("select block_number, count(*) from included_swaps "
                       "group by 1 order by 1").fetchall()
    rep = {r[0] for r in con.execute(
        "select distinct block_number from (select block_number, pool, user, count(*) c "
        "from included_swaps group by 1, 2, 3 having c >= 2)").fetchall()}
    fig, ax = _frame(th)
    ax.bar([r[0] for r in rows], [r[1] for r in rows], width=1.0,
           color=[th["b"] if r[0] in rep else th["a"] for r in rows])
    ax.set_xlabel("block number")
    ax.set_ylabel("V2 swaps in block")
    ax.set_title(f"Swaps per block; red = same wallet hits one pool twice ({len(rep)} of "
                 f"{len(rows)} blocks)", loc="left", fontsize=10)
    fig.savefig(OUT / f"flow-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def size_depth(th: dict, name: str) -> None:
    """Swap size against pool depth in the input token's base units, with iso-impact lines."""
    rows = _con().execute("select x_pre, amount_in from included_swaps "
                          "where x_pre > 0 and amount_in > 0").fetchall()
    xs, ys = zip(*rows)
    fig, ax = _frame(th)
    ax.scatter(xs, ys, s=6, color=th["a"], alpha=0.5, linewidths=0)
    lo, hi = min(xs), max(xs)
    for imp in (1e-4, 1e-2, 1e-1):
        ax.plot([lo, hi], [lo * imp, hi * imp], color=th["b"], ls="--", lw=1)
        ax.annotate(f"impact {imp:g}", (hi, hi * imp), textcoords="offset points",
                    xytext=(-4, 3), ha="right", color=th["fg"], fontsize=8)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("pool depth, input reserve (base units)")
    ax.set_ylabel("swap size (base units)")
    ax.set_title(f"Where swaps sit in size-depth space (n={len(rows):,})", loc="left")
    fig.savefig(OUT / f"sizedepth-{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)


def measured_figures(th: dict, name: str) -> None:
    price_impact(th, name)
    pool_concentration(th, name)
    block_flow(th, name)
    size_depth(th, name)
