"""Guard-visibility figures: slack ECDF and class shares with Wilson 95% intervals.

Reads ``data/guard_estimator.duckdb`` (V3 decodable guards) and the newest ``data/gt_*`` run
(V2 ground truth). Sample sizes go in the labels, never in the bar length.
"""
from __future__ import annotations

import math

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eth_mev.config import DATA_DIR  # noqa: E402

from .measured import OUT, _con, _frame, save  # noqa: E402


def _slacks() -> list[float]:
    con = duckdb.connect(str(DATA_DIR / "guard_estimator.duckdb"), read_only=True)
    rows = sorted(r[0] for r in con.execute("select slack from guard_slacks").fetchall())
    con.close()
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Point share and Wilson score interval for k successes in n."""
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def guard_ecdf(th: dict, name: str) -> None:
    s = _slacks()
    n = len(s)
    q = {p: s[int(p * (n - 1))] for p in (0.5, 0.8, 0.95)}
    fig, ax = _frame(th)
    ax.plot([max(v, 1e-7) * 100 for v in s], [(i + 1) / n for i in range(n)], color=th["a"], lw=2)
    for p, v in q.items():
        ax.axvline(max(v, 1e-7) * 100, color=th["b"], ls="--", lw=1)
        ax.annotate(f"p{int(p * 100)} = {v * 100:.4g}%", (max(v, 1e-7) * 100, p),
                    textcoords="offset points", xytext=(4, -12), color=th["fg"], fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("guard slack: actual out / amountOutMinimum - 1 (%), zeros drawn at 1e-5 %")
    ax.set_ylabel("share of decodable V3 swaps")
    ax.set_title(f"Median decodable guard leaves {q[0.5] * 100:.4f}% of room (n={n:,})",
                 loc="left", fontsize=10)
    save(fig, th, OUT / f"guard-{name}.png")
    plt.close(fig)


def visibility(th: dict, name: str) -> None:
    s = _slacks()
    n3 = len(s)
    classes = [("V3 slack = 0", sum(v == 0 for v in s)),
               ("V3 slack 0 to 0.1%", sum(0 < v <= 1e-3 for v in s)),
               ("V3 slack 0.1% to 1%", sum(1e-3 < v <= 1e-2 for v in s)),
               ("V3 slack > 1%", sum(v > 1e-2 for v in s))]
    n2, k2 = _con().execute("select count(*), count(\"limit\") from included_swaps").fetchone()
    rows = [(f"V2 guard decodable (of n={n2:,} swaps)", k2, n2)]
    rows += [(f"{lab} (of n={n3:,} decodable)", k, n3) for lab, k in classes]
    fig, ax = _frame(th, 7.6, 3.4)
    for i, (lab, k, n) in enumerate(rows[::-1]):
        p, lo, hi = wilson(k, n)
        ax.hlines(i, lo * 100, hi * 100, color=th["a"], lw=3)
        ax.plot(p * 100, i, "o", color=th["b"])
        ax.annotate(f"{p * 100:.1f}%  [{lo * 100:.1f}, {hi * 100:.1f}]", (hi * 100, i),
                    textcoords="offset points", xytext=(6, -3), color=th["fg"], fontsize=8)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]])
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of class (%), Wilson 95% interval")
    ax.set_title("How visible and how loose guards are", loc="left")
    save(fig, th, OUT / f"visibility-{name}.png")
    plt.close(fig)
