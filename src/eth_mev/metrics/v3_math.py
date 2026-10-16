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

# sqrt(1.0001) to high precision — the exact per-tick sqrt-price ratio, so ``sqrt_ratio_x96``
# does not depend on the (easy to mistype) 0xfff... hex-constant table in SqrtPriceMath.sol.
from decimal import Decimal, getcontext as _getcontext  # noqa: E402

_getcontext().prec = 80
_SQRT_10001 = Decimal("1.0001") ** Decimal("0.5")
_DEC_Q96 = Decimal(2) ** 96

# Cache the pow results per tick magnitude (tick ratios are symmetric: ratio(-t) = Q96_2 / ratio(t)).
_sqrt_cache: dict[int, int] = {}


def sqrt_ratio_x96(tick: int) -> int:
    """``getSqrtRatioAtTick`` — sqrt price (token1/token0) as Q64.96, computed **exactly** as
    ``floor(Q96 · sqrt(1.0001)^tick)``. Verified against 1.0001^tick to <1e-12 for the full tick
    range; no dependence on the on-chain hex-constant table."""
    if tick > MAX_TICK or tick < MIN_TICK:
        raise ValueError("tick out of range")
    if tick in _sqrt_cache:
        return _sqrt_cache[tick]
    if tick >= 0:
        v = int((_DEC_Q96 * (_SQRT_10001 ** tick)).to_integral_value(rounding="ROUND_FLOOR"))
    else:
        v = int((_DEC_Q96 * (_SQRT_10001 ** tick)).to_integral_value(rounding="ROUND_FLOOR"))
    _sqrt_cache[tick] = v
    return v


def price_to_sqrt_x96(price: float) -> int:
    """token1/token0 spot price -> sqrtPriceX96 (float in, int fixed-point out)."""
    import math
    return int(math.sqrt(price) * Q96)


def tick_at_sqrt_ratio(sqrt_ratio: int) -> int:
    """``getTickAtSqrtRatio`` — nearest tick with sqrt price <= ``sqrt_ratio``, binary search."""
    if sqrt_ratio <= sqrt_ratio_x96(MIN_TICK):
        return MIN_TICK
    if sqrt_ratio >= sqrt_ratio_x96(MAX_TICK):
        return MAX_TICK
    lo, hi = MIN_TICK, MAX_TICK
    while lo <= hi:
        mid = (lo + hi) >> 1
        if sqrt_ratio_x96(mid) <= sqrt_ratio:
            lo = mid + 1
        else:
            hi = mid - 1
    return hi


# ---- swap step (single initialized range) ----

def amount0_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool) -> int:
    """amount0 moved when price goes sqrt_a→sqrt_b: ``L·(√Pb−√Pa)·Q96 / (√Pa·√Pb)``."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if sqrt_a == 0:
        return 0
    numerator = liquidity * (sqrt_b - sqrt_a) * Q96
    denominator = sqrt_a * sqrt_b
    if round_up:
        return (numerator + denominator - 1) // denominator
    return numerator // denominator


def amount1_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool) -> int:
    """amount1 moved when price goes sqrt_a→sqrt_b: ``L·(√Pb−√Pa) / Q96``."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    numerator = liquidity * (sqrt_b - sqrt_a)
    if round_up:
        return (numerator + Q96 - 1) // Q96
    return numerator // Q96


def _next_sqrt_from_token0_in(sqrt_cur: int, liquidity: int, amount0: int) -> int:
    """sqrt price after ``amount0`` of token0 in (price falls). Mirrors SqrtPriceMath."""
    # 1/√P_next = 1/√P + amount0 / (L·2^96)  ⇒  √P_next = L·√P·2^96 / (L·2^96 + amount0·√P)
    numerator = sqrt_cur * liquidity * Q96
    denominator = liquidity * Q96 + amount0 * sqrt_cur
    return numerator // denominator


def _next_sqrt_from_token1_in(sqrt_cur: int, liquidity: int, amount1: int) -> int:
    """sqrt price after ``amount1`` of token1 in (price rises)."""
    return sqrt_cur + (amount1 * Q96) // liquidity


def input_to_output(amount_in: int, sqrt_cur: int, liquidity: int, zero_for_one: bool,
                    fee_ppm: int) -> tuple[int, int]:
    """One-range swap: ``(amount_out, sqrt_next)`` for ``amount_in`` (fees deducted).

    ``zero_for_one=True`` pays token0 (price falls); False pays token1 (price rises).
    """
    amt = int(amount_in * (1 - fee_ppm / 1_000_000))
    if zero_for_one:
        sqrt_next = _next_sqrt_from_token0_in(sqrt_cur, liquidity, amt)
        amount_out = amount1_delta(sqrt_next, sqrt_cur, liquidity, False)
    else:
        sqrt_next = _next_sqrt_from_token1_in(sqrt_cur, liquidity, amt)
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
    if zero_for_one:
        order = sorted((t for t in ticks if t < cur_tick), reverse=True)
    else:
        order = sorted((t for t in ticks if t > cur_tick))
    for nxt_tick in order:
        if amount_remaining <= 0:
            break
        target = sqrt_ratio_x96(nxt_tick)
        if zero_for_one:
            a_to = amount0_delta(target, cur, L, True)
        else:
            a_to = amount1_delta(cur, target, L, True)
        if amount_remaining >= a_to:
            if zero_for_one:
                amount_out += amount1_delta(target, cur, L, False)
            else:
                amount_out += amount0_delta(cur, target, L, False)
            amount_remaining -= a_to
            cur = target
            L += ticks.get(nxt_tick, 0)
            cur_tick = nxt_tick
        else:
            out, cur = input_to_output(amount_remaining, cur, L, zero_for_one, 0)
            amount_out += out
            amount_remaining = 0
            break
    if amount_remaining > 0 and L > 0:
        out, cur = input_to_output(amount_remaining, cur, L, zero_for_one, 0)
        amount_out += out
    return amount_out, cur


def output_to_input(amount_out_wanted: int, sqrt_price: int, liquidity: int,
                    ticks: dict[int, int], tick_spacing: int, zero_for_one: bool,
                    fee_ppm: int) -> int:
    """The input amount needed to produce ``amount_out_wanted`` (exact-output), binary search over
    ``exact_input`` (input side carries the fee)."""
    if amount_out_wanted <= 0:
        return 0
    lo, hi = 0, 1
    # exponential doubling to bracket the input, then converge
    while exact_input(sqrt_price, liquidity, ticks, tick_spacing, hi, zero_for_one, fee_ppm)[0] < amount_out_wanted:
        hi *= 2
        if hi > MAX_UINT256:
            return MAX_UINT256
    for _ in range(256):
        mid = (lo + hi) // 2
        if exact_input(sqrt_price, liquidity, ticks, tick_spacing, mid, zero_for_one, fee_ppm)[0] >= amount_out_wanted:
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1:
            break
    return hi
