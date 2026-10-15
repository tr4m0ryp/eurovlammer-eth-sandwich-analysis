"""How exposed one swap is to a sandwich: its slippage slack and the attacker's best profit.

The victim's protection is the limit in its own instruction. A sandwich front-runs it by ``D``
on the same side, lets it execute at the worse price, then unwinds. The attacker pays the pool's
fee on both legs, so the same victim is worth less on a 125 bps pool than on a 30 bps pool:
the fee enters the profit, not a separate filter.

    profit(D) = sell_back(D) * (1 - r) - D * (1 + r)       subject to: victim limit still holds

The feasible ``D`` is an interval ``[0, D_max]`` (the victim's price worsens monotonically in
``D``), found by bisection; profit is unimodal on it and maximised by golden section.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..chain.swaps.model import EXACT_IN, EXACT_OUT, Swap
from .curve import net_for_tokens, sol_for_tokens, tokens_for_net

GOLD = (math.sqrt(5) - 1) / 2
# Attacker capital per front leg. Real fronts (5 h of entity-filtered sandwiches, 2026-10-14): median
# 0.03 SOL (buy) / 0.36 SOL (sell), p99 3.0 SOL, max 6.9 SOL. Without a cap a victim with no limit
# "yields" whatever an attacker dumping half the pool could take, which no bot has the inventory for.
CAPITAL_LAMPORTS = 5e9
INF = float("inf")


@dataclass
class Exposure:
    slack: float          # fractional worsening of the victim's price its limit allows (inf = none)
    mesp: float           # max extractable sandwich profit, lamports, after pool fees on both legs
    front: float          # attacker's optimal front-run size (lamports net, or tokens on a sell)
    limited: bool         # False when the victim set no effective limit


def _victim_buy(s: Swap, r: float):
    """(net lamports in, tokens out, feasible) for the victim at state (x, y)."""
    if s.kind == EXACT_OUT:
        a, cap = float(s.amount), s.limit

        def run(x, y):
            n = net_for_tokens(x, y, a)
            return n, a, cap is None or n * (1 + r) <= cap
    else:
        n0, floor = float(s.amount) / (1 + r), s.limit

        def run(x, y):
            t = tokens_for_net(x, y, n0)
            return n0, t, floor is None or t >= floor
    return run


def _profit_buy(s: Swap, r: float, x: float, y: float):
    victim = _victim_buy(s, r)

    def f(d: float) -> tuple[float, bool]:
        t1 = tokens_for_net(x, y, d)
        x1, y1 = x + d, y - t1
        n, t, ok = victim(x1, y1)
        x2, y2 = x1 + n, y1 - t
        return sol_for_tokens(x2, y2, t1) * (1 - r) - d * (1 + r), ok
    return f


def _profit_sell(s: Swap, r: float, x: float, y: float):
    a, floor = float(s.amount), s.limit

    def f(t: float) -> tuple[float, bool]:
        o1 = sol_for_tokens(x, y, t)
        x1, y1 = x - o1, y + t
        ov = sol_for_tokens(x1, y1, a)
        x2, y2 = x1 - ov, y1 + a
        back = net_for_tokens(x2, y2, t)
        return o1 * (1 - r) - back * (1 + r), floor is None or ov * (1 - r) >= floor
    return f


def slack(s: Swap, x: float, y: float) -> float:
    """How far the victim's average price may worsen from state (x, y) before its limit binds."""
    r = s.fee_bps / 1e4
    if s.limit is None or s.amount is None:
        return math.nan
    if s.is_buy and s.kind == EXACT_OUT:
        cost = net_for_tokens(x, y, s.amount) * (1 + r)
        return s.limit / cost - 1 if cost > 0 else math.nan
    if s.is_buy:
        t = tokens_for_net(x, y, s.amount / (1 + r))
        return t / s.limit - 1 if s.limit > 1 else INF
    out = sol_for_tokens(x, y, s.amount) * (1 - r)
    return out / s.limit - 1 if s.limit > 1 else INF


def exposure(s: Swap, x: float | None = None, y: float | None = None) -> Exposure:
    """Exposure of ``s`` at state (x, y), its own pre-state by default, with 5 SOL capital cap."""
    return _exposure(s, CAPITAL_LAMPORTS, x, y)


def exposure_budget(s: Swap, budget_lamports: float, x: float | None = None, y: float | None = None) -> Exposure:
    """Exposure with a custom capital budget (lamports) instead of the default 5 SOL cap."""
    return _exposure(s, budget_lamports, x, y)


def _exposure(s: Swap, capital: float, x: float | None = None, y: float | None = None) -> Exposure:
    """Exposure of ``s`` at state (x, y), its own pre-state by default, capped at ``capital`` lamports."""
    x = s.x_pre if x is None else x
    y = s.y_pre if y is None else y
    r = s.fee_bps / 1e4
    sl = slack(s, x, y)
    if s.amount is None or x <= 0 or y <= 0:
        return Exposure(sl, 0.0, 0.0, False)
    if s.is_buy:
        f, hi = _profit_buy(s, r, x, y), capital
    else:   # tokens worth at most capital of curve output
        cap = y * capital / (x - capital) if capital < x else 0.5 * y
        f, hi = _profit_sell(s, r, x, y), min(0.5 * y, cap)
    if not f(0.0)[1]:
        return Exposure(sl, 0.0, 0.0, True)
    d_max = hi
    if not f(hi)[1]:
        lo = 0.0
        for _ in range(60):
            mid = (lo + d_max) / 2
            lo, d_max = (mid, d_max) if f(mid)[1] else (lo, mid)
        d_max = lo
    a, b = 0.0, d_max
    for _ in range(80):
        c, d = b - GOLD * (b - a), a + GOLD * (b - a)
        if f(c)[0] < f(d)[0]:
            a = c
        else:
            b = d
    best = (a + b) / 2
    p = f(best)[0]
    return Exposure(sl, max(p, 0.0), best if p > 0 else 0.0, math.isfinite(sl))
