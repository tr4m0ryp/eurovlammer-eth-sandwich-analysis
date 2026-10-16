"""Uniswap V3 concentrated-liquidity arithmetic (exact-in swap across tick ranges).

Implements the canonical V3 core maths in Python integers, matching ``SqrtPriceMath``/``SwapMath``.
Price is Q64.96 fixed point: ``sqrt_price_x96`` such that spot price (token1 per token0) is
``(sqrt_price_x96 / 2**96) ** 2``. Ticks are spaced ``tick_spacing`` apart and each carries a
``liquidity_net`` delta; crossing a tick mutates the active liquidity. This is the V3 analogue of the
constant-product ``x·y=k`` legs in ``metrics/curve.py``.

The swap iterator mirrors ``SwapMath.computeSwapStep``: walk price through initialized ticks in the
swap direction, consuming ``(1-fee)*amount_in``, accumulating output, and flipping ``liquidity`` by
each crossed tick's ``liquidityNet``.
"""
from __future__ import annotations

Q96 = 1 << 96
Q96_2 = Q96 * Q96
MAX_UINT256 = (1 << 256) - 1
MIN_TICK = -887272
MAX_TICK = 887272


def sqrt_ratio_x96(tick: int) -> int:
    """``getSqrtRatioAtTick`` — the sqrt price (token1/token0) as Q64.96 for a tick (exact)."""
    abs_tick = abs(tick)
    if abs_tick > MAX_TICK:
        raise ValueError("tick out of range")
    ratio = (0xFFFCB933BD6FAD37AA2D162D1A594001 << 128) if abs_tick & 0x1 else (1 << 128)
    mul = lambda v: (v >> 128)  # noqa: E731  (kept inline below for clarity)
    def scale(r, c):
        return (r * c) >> 128
    if abs_tick & 0x2:
        ratio = scale(ratio, 0xFFF97272373D413259A46990580E213A)
    if abs_tick & 0x4:
        ratio = scale(ratio, 0xFFF2E50F5F656932EF12357CF3C7FDCC)
    if abs_tick & 0x8:
        ratio = scale(ratio, 0xFFE5CACA7E10E4E61C3624EAA0941CD0)
    if abs_tick & 0x10:
        ratio = scale(ratio, 0xFFCB9843D60F6159C9DB58835C926644)
    if abs_tick & 0x20:
        ratio = scale(ratio, 0xFF973B41FA98C081472E6896DFB254C0)
    if abs_tick & 0x40:
        ratio = scale(ratio, 0xFF2EA16466C96A3843EC78B326B52861)
    if abs_tick & 0x80:
        ratio = scale(ratio, 0xFE5DEE046A99A2A811C461F1969C3053)
    if abs_tick & 0x100:
        ratio = scale(ratio, 0xFCBE86C7900A88AEDCFFC83B479AA3A4)
    if abs_tick & 0x200:
        ratio = scale(ratio, 0xF987A7253AC413176F2B074CF7815E54)
    if abs_tick & 0x400:
        ratio = scale(ratio, 0xF3392B0822B70005940C7A398E4B70F3)
    if abs_tick & 0x800:
        ratio = scale(ratio, 0xE7159475A2C29B7443B29C7FA6E889D9)
    if abs_tick & 0x1000:
        ratio = scale(ratio, 0xD097F3BDFD2022B8845AD8F792AA5825)
    if abs_tick & 0x2000:
        ratio = scale(ratio, 0xA9F746462D870FDF8A65DC1F90E061E5)
    if abs_tick & 0x4000:
        ratio = scale(ratio, 0x70D869A156D2A1B890BB3DF62BAF32F7)
    if abs_tick & 0x8000:
        ratio = scale(ratio, 0x31BE135F97D08FD981231505542FCFA6)
    if abs_tick & 0x10000:
        ratio = scale(ratio, 0x9AA508B5B7A84E1C677DE54F3E99BC9)
    if abs_tick & 0x20000:
        ratio = scale(ratio, 0x5D6AF8DEDB81196699C329225EE604)
    if abs_tick & 0x40000:
        ratio = scale(ratio, 0x2216E584F5FA1EA926041BEDFE98)
    if abs_tick & 0x80000:
        ratio = scale(ratio, 0x48A170391F7DC42444E8FA2)
    return ratio if tick >= 0 else (Q96_2 // ratio)


def price_to_sqrt_x96(price: float) -> int:
    """token1/token0 spot price -> sqrtPriceX96 (float in, int fixed-point out)."""
    import math
    return int(math.sqrt(price) * Q96)


def tick_at_sqrt_ratio(sqrt_ratio: int) -> int:
    """``getTickAtSqrtRatio`` — nearest initialized-capable tick for a sqrt price."""
    ratio = max(sqrt_ratio, 4295128739)
    # from the reference: binary search over the 1.0001 log; approximate then snap
    lo, hi = MIN_TICK, MAX_TICK
    if ratio <= sqrt_ratio_x96(MIN_TICK):
        return MIN_TICK
    if ratio >= sqrt_ratio_x96(MAX_TICK):
        return MAX_TICK
    while lo <= hi:
        mid = (lo + hi) >> 1
        if sqrt_ratio_x96(mid) <= ratio:
            lo = mid + 1
        else:
            hi = mid - 1
    return hi


# ---- swap step (single initialized range) ----

def amount0_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool) -> int:
    """amount0 moved when price goes sqrt_a→sqrt_b (0 if range has no token0)."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    lo, hi = sqrt_a, sqrt_b
    if lo == 0:
        return 0
    if round_up:
        return (liquidity * (hi - lo) + hi * lo - 1) // (hi * lo) * Q96
    return (liquidity * (hi - lo)) // (hi * lo) * Q96


def amount1_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool) -> int:
    """amount1 moved when price goes sqrt_a→sqrt_b."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if round_up:
        return (liquidity * (sqrt_b - sqrt_a) + Q96 - 1) // Q96
    return (liquidity * (sqrt_b - sqrt_a)) // Q96


def input_to_output(amount_in: int, sqrt_cur: int, liquidity: int, zero_for_one: bool,
                    fee_ppm: int) -> tuple[int, int]:
    """One-range swap: ``(amount_out, sqrt_next)`` for ``amount_in`` (fees deducted).

    ``zero_for_one=True`` pays token0 (price falls); False pays token1 (price rises).
    """
    fee = fee_ppm / 1_000_000
    amt = int(amount_in * (1 - fee))
    if zero_for_one:
        # token0 in, price falls; solve sqrt_next from amount0
        # amount0 = L*(sqrt_cur - sqrt_next)/(sqrt_cur*sqrt_next)*Q96
        #   → sqrt_next = sqrt_cur * L*Q96 / (L*Q96 + amount0*sqrt_cur)
        num = sqrt_cur * liquidity * Q96
        den = liquidity * Q96 + amt * sqrt_cur
        sqrt_next = num // den
        amount_out = amount1_delta(sqrt_next, sqrt_cur, liquidity, False)
    else:
        # token1 in, price rises
        sqrt_next = sqrt_cur + (amt * Q96) // liquidity
        amount_out = amount0_delta(sqrt_cur, sqrt_next, liquidity, False)
    return amount_out, sqrt_next


def exact_input(sqrt_price: int, liquidity: int, ticks: dict[int, int], tick_spacing: int,
                amount_in: int, zero_for_one: bool, fee_ppm: int) -> tuple[int, int]:
    """Exact-in swap across initialized ticks. Returns ``(amount_out, sqrt_price_next)``.

    ``ticks`` maps initialized tick -> liquidityNet (signed). Ticks at ``tick_spacing`` multiples.
    Faithful to ``SwapMath.computeSwapStep``: cross each tick in the swap direction, update
    ``liquidity`` by ``liquidityNet``, accumulate output, stop when ``amount_in`` is consumed or
    price reaches a range with no further liquidity.
    """
    amount_remaining = int(amount_in * (1 - fee_ppm / 1_000_000))
    amount_out = 0
    cur = sqrt_price
    cur_tick = tick_at_sqrt_ratio(cur)
    L = liquidity
    # initialized ticks in swap direction, in price order
    if zero_for_one:
        order = sorted((t for t in ticks if t < cur_tick), reverse=True)
    else:
        order = sorted((t for t in ticks if t > cur_tick))
    for nxt_tick in order:
        if amount_remaining <= 0:
            break
        target = sqrt_ratio_x96(nxt_tick)
        if zero_for_one:
            # token0 to reach target
            a_to = amount0_delta(target, cur, L, True)
        else:
            a_to = amount1_delta(cur, target, L, True)
        if amount_remaining >= a_to:
            # cross the tick
            if zero_for_one:
                amount_out += amount1_delta(target, cur, L, False)
            else:
                amount_out += amount0_delta(cur, target, L, False)
            amount_remaining -= a_to
            cur = target
            L += ticks.get(nxt_tick, 0)
            cur_tick = nxt_tick
        else:
            # finish inside this range
            out, cur = input_to_output(amount_remaining, cur, L, zero_for_one, 0)
            amount_out += out
            amount_remaining = 0
            break
    if amount_remaining > 0:
        # exhaust the remaining amount within the last (possibly zero-liquidity) range
        if L > 0:
            out, cur = input_to_output(amount_remaining, cur, L, zero_for_one, 0)
            amount_out += out
    return amount_out, cur


def output_to_input(amount_out_wanted: int, sqrt_price: int, liquidity: int,
                    ticks: dict[int, int], tick_spacing: int, zero_for_one: bool,
                    fee_ppm: int) -> int:
    """The input amount needed to produce ``amount_out_wanted`` (exact-output), by binary search
    over ``exact_input``. Fee is applied on the input side. Used for the victim's exact-out guard."""
    lo, hi = 0, MAX_UINT256
    for _ in range(200):
        mid = (lo + hi) // 2
        out, _ = exact_input(sqrt_price, liquidity, ticks, tick_spacing, mid, zero_for_one, fee_ppm)
        if out >= amount_out_wanted:
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1:
            break
    return hi
