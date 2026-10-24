"""Unit tests: V3 concentrated-liquidity sandwich exposure (golden-section MESP)."""
from __future__ import annotations

from eth_mev.metrics.v3.exposure import V3Pool, exposure
from eth_mev.metrics.v3.tick_math import sqrt_ratio_x96


def _pool(L=2 * 10 ** 21, fee_ppm=3000):
    return V3Pool(sqrt_price_x96=sqrt_ratio_x96(0), liquidity=L, tick=0, tick_spacing=60,
                  fee_ppm=fee_ppm, ticks={})


def test_no_sandwich_when_guard_tight():
    # victim's 3% guard is tighter than their own ~5% price impact -> reverts before any sandwich
    victim = {"zero_for_one": True, "amount_in": 100 * 10 ** 18, "limit": int(0.97 * 100 * 0.997e18)}
    e = exposure(_pool(), victim, capital=int(5e18))
    assert e.mesp == 0.0
    assert e.limited


def test_positive_mesp_with_room():
    victim = {"zero_for_one": True, "amount_in": 100 * 10 ** 18, "limit": int(0.90 * 100 * 0.997e18)}
    e = exposure(_pool(), victim, capital=int(5e18))
    assert e.mesp > 0
    assert 0 < e.front <= 5e18          # front capped at capital
    assert e.slack > 0


def test_mesp_scales_with_slack_roughly():
    # more room -> more extractable profit (monotone-ish)
    tight = exposure(_pool(), {"zero_for_one": True, "amount_in": 100 * 10 ** 18,
                               "limit": int(0.90 * 100 * 0.997e18)}, capital=int(5e18)).mesp
    loose = exposure(_pool(), {"zero_for_one": True, "amount_in": 100 * 10 ** 18,
                               "limit": int(0.80 * 100 * 0.997e18)}, capital=int(5e18)).mesp
    assert loose >= tight


def test_degenerate_pool_returns_zero():
    # zero liquidity -> no output -> no sandwich
    pool = V3Pool(sqrt_ratio_x96(0), 0, 0, 60, 3000, {})
    victim = {"zero_for_one": True, "amount_in": 10 ** 18, "limit": 0}
    e = exposure(pool, victim, capital=int(1e18))
    assert e.mesp == 0.0


def test_capital_cap_binds():
    victim = {"zero_for_one": True, "amount_in": 100 * 10 ** 18, "limit": int(0.80 * 100 * 0.997e18)}
    small = exposure(_pool(), victim, capital=int(1e18)).front
    big = exposure(_pool(), victim, capital=int(10e18)).front
    assert small <= 1e18 + 1e9
    assert big <= 10e18 + 1e9
