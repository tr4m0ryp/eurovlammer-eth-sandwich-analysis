"""Unit tests: Uniswap V3 concentrated-liquidity maths (exact tick/swap figures)."""
from __future__ import annotations

from eth_mev.measure.exposure.v3.tick_math import (
    Q96, amount0_delta, amount1_delta, exact_input, input_to_output, output_to_input,
    price_to_sqrt_x96, sqrt_ratio_x96, tick_at_sqrt_ratio,
)


def test_sqrt_ratio_at_tick_zero_is_q96():
    assert sqrt_ratio_x96(0) == Q96


def test_tick_sqrt_roundtrip_consistent():
    for t in (-887272, -100000, -100, -1, 0, 1, 100, 100000, 887272):
        r = sqrt_ratio_x96(t)
        assert tick_at_sqrt_ratio(r) == t


def test_sqrt_ratio_monotonic():
    assert sqrt_ratio_x96(0) < sqrt_ratio_x96(100) < sqrt_ratio_x96(1000)
    assert sqrt_ratio_x96(-1000) < sqrt_ratio_x96(-100) < sqrt_ratio_x96(0)


def test_price_equals_10001_pow_tick():
    """The sqrt-price ratio must reproduce 1.0001**tick as the spot price."""
    from decimal import Decimal, getcontext
    getcontext().prec = 60
    base = Decimal("1.0001")
    q96 = Decimal(2) ** 96
    for t in (0, 100, 10000, -100):
        price = (Decimal(sqrt_ratio_x96(t)) / q96) ** 2
        expect = base ** t
        assert abs(float(price / expect) - 1.0) < 1e-12


def test_price_to_sqrt_x96_roundtrip():
    p = price_to_sqrt_x96(1.0)
    assert p == Q96
    p2 = price_to_sqrt_x96(100.0)  # price 100 token1/token0
    assert p2 > Q96


def test_input_to_output_tiny_swap_matches_constant_product_limit():
    """At tick 0 with huge liquidity, a swap is ~ (1-fee)*amount_in, price impact negligible."""
    L = 10 ** 30
    sqrt0 = sqrt_ratio_x96(0)
    out, sqrt_next = input_to_output(1000, sqrt0, L, zero_for_one=True, fee_ppm=3000)
    assert out == 997                       # 1000 * (1 - 0.003)
    assert sqrt_next < sqrt0                # price fell (token0 in)


def test_input_to_output_both_directions_symmetry():
    L = 10 ** 30
    sqrt0 = sqrt_ratio_x96(0)
    amt = 10 ** 18   # realistic: swap ~1e-12 of liquidity, so rounding is negligible
    out_down, _ = input_to_output(amt, sqrt0, L, True, 3000)
    out_up, _ = input_to_output(amt, sqrt0, L, False, 3000)
    expected = int(amt * 0.997)
    assert out_down == 996_999_999_999_006_002
    # both directions within a few wei of (1-fee)*amount
    assert abs(out_down - expected) < 1_000_000
    assert abs(out_up - expected) < 1_000_000


def test_exact_input_across_tick_matches_fee_net():
    L = 10 ** 30
    sqrt0 = sqrt_ratio_x96(0)
    amt = 10 ** 18
    out, _ = exact_input(sqrt0, L, {-60: 10 ** 30}, 60, amt, True, 3000)
    assert out == 996_999_999_999_006_002     # pinned: (1 - 0.003) * 1e18 minus rounding


def test_output_to_input_roundtrip():
    L = 10 ** 30
    sqrt0 = sqrt_ratio_x96(0)
    out, _ = exact_input(sqrt0, L, {-60: 10 ** 30}, 60, 10 ** 18, True, 3000)
    back = output_to_input(out, sqrt0, L, {-60: 10 ** 30}, 60, True, 3000)
    assert abs(back - 10 ** 18) < 1000       # inverse within rounding


def test_deltas_sign_and_magnitude():
    L = 10 ** 6
    sqrt0 = sqrt_ratio_x96(0)
    sqrt_neg = sqrt_ratio_x96(-60)
    a0 = amount0_delta(sqrt_neg, sqrt0, L, True)
    a1 = amount1_delta(sqrt_neg, sqrt0, L, False)
    assert a0 > 0 and a1 > 0
    # price fell 0.3% on 1e6 liquidity -> ~3000 of each, within rounding
    assert 2900 < a0 < 3100
    assert 2900 < a1 < 3100
