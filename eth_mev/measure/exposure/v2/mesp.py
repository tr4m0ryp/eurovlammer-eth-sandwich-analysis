"""How exposed one V2 swap is to a sandwich: its slippage slack and the attacker's best profit.

The victim's protection is the limit in its own calldata (``amountOutMin``). A sandwich front-runs it
by ``D`` on the same side, lets it execute at the worse price, then unwinds. The attacker pays the
pool's fee on both legs, so the same victim is worth less on a higher-fee fork.

    profit(D) = sell_back(D) * (1 - r) - D * (1 + r)       subject to: victim limit still holds

The feasible ``D`` is an interval ``[0, D_max]`` (the victim's price worsens monotonically in ``D``),
found by bisection; profit is unimodal on it and maximised by golden section. This is the verbatim
golden-section MESP from the Solana metrics, remapped: ``amountIn -> amount``, ``amountOutMin -> limit``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ....onchain.decode.v2 import EXACT_OUT
from ....onchain.decode.v2.swaps import Swap
from .curve import net_for_tokens, sol_for_tokens, tokens_for_net

GOLD = (math.sqrt(5) - 1) / 2
# Attacker capital per front leg (in numeraire wei). Without a cap a victim with no limit "yields"
# whatever an attacker dumping half the pool could take, which no bot has the inventory for. Keep the
# same role as the Solana CAPITAL_LAMPORTS cap; the strict screener passes its own wei budget.
CAPITAL_WEI = 5e18
INF = float("inf")


@dataclass
class Exposure:
    slack: float          # fractional worsening of the victim's price its limit allows (inf = none)
    mesp: float           # max extractable sandwich profit, wei, after pool fees on both legs
    front: float          # attacker's optimal front-run size (numeraire wei net, or tokens on a sell)
    limited: bool         # False when the victim set no effective limit


def _victim_buy(s: Swap, r: float):
    """(net numeraire in, tokens out, feasible) for the victim at state (x, y)."""
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


def exposure(s: Swap, x: float | None = None, y: float | None = None, capital: float = CAPITAL_WEI) -> Exposure:
    """Exposure of ``s`` at state (x, y), its own pre-state by default, with the front leg capped at
    ``capital`` numeraire wei (the strict screener passes its own budget)."""
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


def _swap_from_row(d: dict) -> Swap:
    """Rebuild a minimal ``Swap`` from a plain row dict (the strict/scorer row shape)."""
    opt = lambda k: int(d[k]) if d.get(k) is not None else None  # noqa: E731
    return Swap(
        tx_hash=d.get("tx_hash", ""), block_number=d.get("block"), tx_index=d.get("tx_index", 0),
        log_index=d.get("log_index", 0), venue=d.get("venue", ""), pool=d.get("pool", ""),
        token0=d.get("token0", ""), token1=d.get("token1", ""), user=d.get("user", ""),
        is_buy=bool(d.get("is_buy")), amount_in=int(d.get("amount_in") or 0),
        amount_out=int(d.get("amount_out") or 0), x_pre=float(d.get("x_pre") or 0.0),
        y_pre=float(d.get("y_pre") or 0.0), fee_bps=float(d.get("fee_bps") or 30.0),
        kind=d.get("kind", "in"), amount=opt("amount"), limit=opt("limit"),
        deadline=d.get("deadline"))
