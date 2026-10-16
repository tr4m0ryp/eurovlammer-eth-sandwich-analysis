"""V3 concentrated-liquidity sandwich exposure: the attacker's best profit, golden-section MESP.

The victim's protection on V3 is ``amountOutMinimum`` in the router call — the same slippage-guard role
V2's ``amountOutMin`` plays. A sandwich is three legs on the *same pool state, in order*: the attacker
front-runs the victim by ``D`` numeraire (moving the tick), the victim executes at the worse price, then
the attacker unwinds (back-runs) and pockets the spread minus both legs' pool fees.

Because V3 liquidity is a step function of tick, the price/amount function is not a smooth curve, but
within a single tick range the profit is unimodal in ``D`` — so the same golden-section search as the
V2 exposure applies, using the exact V3 swap arithmetic in ``v3_math``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import v3_math as m

GOLD = (math.sqrt(5) - 1) / 2
CAPITAL_WEI = int(5e18)
INF = float("inf")


@dataclass
class V3Pool:
    """The on-chain state the exposure math needs for one pool."""
    sqrt_price_x96: int
    liquidity: int
    tick: int
    tick_spacing: int
    fee_ppm: int                  # fee tier in parts-per-million (3000 = 0.3%)
    ticks: dict[int, int]         # initialized tick -> liquidityNet (signed)
    token0: str = ""
    token1: str = ""
    numeraire_is_token0: bool = True

    @property
    def spot_price(self) -> float:
        return (self.sqrt_price_x96 / m.Q96) ** 2   # token1 per token0


@dataclass
class V3Exposure:
    slack: float    # fractional worsening the victim's price allows before its guard binds (inf = none)
    mesp: float     # max extractable sandwich profit (numeraire wei), after both legs' fees
    front: float    # attacker's optimal front-run size (numeraire wei)
    limited: bool   # False when the victim set no effective limit


def _swap_amount_out(pool: V3Pool, amount_in: int, zero_for_one: bool) -> int:
    return m.exact_input(pool.sqrt_price_x96, pool.liquidity, pool.ticks, pool.tick_spacing,
                         amount_in, zero_for_one, pool.fee_ppm)[0]


def _swap_amount_in(pool: V3Pool, amount_out_wanted: int, zero_for_one: bool) -> int:
    return m.output_to_input(amount_out_wanted, pool.sqrt_price_x96, pool.liquidity, pool.ticks,
                             pool.tick_spacing, zero_for_one, pool.fee_ppm)


def _state_after(pool: V3Pool, amount_in: int, zero_for_one: bool) -> V3Pool:
    """Return the pool state after swapping ``amount_in`` (updated sqrt price; liquidity is carried
    through and the crossed-tick liquidityNet deltas are re-applied by ``exact_input`` on the next
    leg, so we keep the static ``ticks`` map and only advance ``sqrt_price_x96``)."""
    _, sqrt_next = m.exact_input(pool.sqrt_price_x96, pool.liquidity, pool.ticks, pool.tick_spacing,
                                 amount_in, zero_for_one, pool.fee_ppm)
    return V3Pool(sqrt_next, pool.liquidity, m.tick_at_sqrt_ratio(sqrt_next), pool.tick_spacing,
                  pool.fee_ppm, pool.ticks, pool.token0, pool.token1,
                  numeraire_is_token0=pool.numeraire_is_token0)


def exposure(pool: V3Pool, victim: dict, capital: int = CAPITAL_WEI) -> V3Exposure:
    """Sandwich exposure of a victim swap at ``pool``'s state, front leg capped at ``capital``.

    ``victim``: ``zero_for_one`` (direction; True = numeraire(token0) in), ``amount_in`` (exact-in),
    ``limit`` (amountOutMinimum / amountInMaximum guard). State evolves front → victim → back.
    """
    zfo = bool(victim.get("zero_for_one", pool.numeraire_is_token0))
    amount_in = victim.get("amount_in")
    limit = victim.get("limit")
    if amount_in is None or amount_in <= 0:
        return V3Exposure(math.nan, 0.0, 0.0, False)
    if limit is None:
        limit = 0

    # Victim feasibility at the CURRENT (un-manipulated) price: exact-in must yield >= its guard.
    current_out = _swap_amount_out(pool, amount_in, zfo)
    if current_out < limit:
        return V3Exposure(0.0, 0.0, 0.0, True)     # already reverted; no sandwich possible

    def profit(d: int) -> tuple[float, bool]:
        """Sandwich profit for a front-run of ``d`` numeraire, evolving state front → victim → back."""
        # 1. front leg: attacker buys the token with ``d`` numeraire → token received, state moves
        front_tok = _swap_amount_out(pool, d, zfo)
        front_pool = _state_after(pool, d, zfo)
        # 2. victim executes at the manipulated price: exact-in must still clear its guard
        victim_out = _swap_amount_out(front_pool, amount_in, zfo)
        if victim_out < limit:
            return 0.0, False
        post_victim = _state_after(front_pool, amount_in, zfo)
        # 3. back leg: the attacker unwinds (reverse direction) at the post-victim price, capturing
        #    the spread the victim's own size created.
        back_numeraire = _swap_amount_out(post_victim, front_tok, not zfo)
        return float(back_numeraire - d), True

    # Feasible front interval [0, D_max]: binary-search the boundary where the victim's guard binds.
    if not profit(0)[1]:
        return V3Exposure(0.0, 0.0, 0.0, True)
    lo, d_max = 0, capital
    if not profit(capital)[1]:
        lo, hi = 0, capital
        for _ in range(80):
            mid = (lo + hi) // 2
            if profit(mid)[1]:
                lo = mid
            else:
                hi = mid
        d_max = lo
    # golden-section maximize profit over [0, d_max]
    a, b = 0, d_max
    for _ in range(80):
        c = b - int(GOLD * (b - a))
        dd = a + int(GOLD * (b - a))
        if profit(c)[0] < profit(dd)[0]:
            a = c
        else:
            b = dd
        if b - a <= 2:
            break
    best = (a + b) // 2
    p = max(profit(best)[0], 0.0)
    # probe the midpoint too (tick-crossing can make profit non-unimodal)
    mid2 = d_max // 2
    pm2 = profit(mid2)[0]
    if pm2 > p:
        best, p = mid2, pm2
    sl = math.inf if limit == 0 else current_out / limit - 1
    return V3Exposure(sl, p, best if p > 0 else 0.0, limit > 0)
